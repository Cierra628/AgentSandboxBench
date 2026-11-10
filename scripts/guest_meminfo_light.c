/* Meminfo-only sampler for fixed guests without Python. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

static const char *fields[] = {
    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SReclaimable",
    "Shmem", "AnonPages", "Slab", "SUnreclaim", "KernelStack", "PageTables",
    "Percpu", "Active(file)", "Inactive(file)", "Dirty"
};
#define FIELDS (sizeof(fields)/sizeof(fields[0]))

static long long clock_ns(clockid_t clock) {
    struct timespec now;
    if (clock_gettime(clock, &now)) abort();
    return (long long)now.tv_sec * 1000000000LL + now.tv_nsec;
}

static int meminfo(unsigned long long values[FIELDS]) {
    unsigned int seen = 0;
    FILE *file = fopen("/proc/meminfo", "r");
    if (!file) return -1;
    char line[256], key[128], unit[16];
    unsigned long long value;
    while (fgets(line, sizeof(line), file)) {
        if (sscanf(line, "%127[^:]: %llu %15s", key, &value, unit) != 3) continue;
        for (size_t i = 0; i < FIELDS; ++i) {
            if (!strcmp(fields[i], key) && !strcmp(unit, "kB")) {
                values[i] = value;
                seen |= 1u << i;
            }
        }
    }
    int error = ferror(file);
    fclose(file);
    return !error && seen == ((1u << FIELDS) - 1) ? 0 : -1;
}

static double seconds(struct timeval value) {
    return value.tv_sec + value.tv_usec / 1e6;
}

int main(int argc, char **argv) {
    if (argc != 5) return 64;
    char *end;
    long limit = strtol(argv[4], &end, 10);
    if (*end || limit < 1 || limit > 17400) return 64;
    if (mkdir(argv[1], 0700) || chdir(argv[1])) return 1;
    FILE *table = fopen("guest-memory.tsv", "wx");
    if (!table) return 1;
    fputs("timestamp_ns\tmonotonic_ns\telapsed_ns", table);
    for (size_t i = 0; i < FIELDS; ++i) {
        fputc('\t', table);
        for (const char *c = fields[i]; *c; ++c) {
            if (*c != ')') fputc(*c == '(' ? '_' : *c, table);
        }
        fputs("_kib", table);
    }
    fputc('\n', table);
    long long start = clock_ns(CLOCK_MONOTONIC), previous = 0, max_gap = 0;
    unsigned int count = 0;
    int success = 0;
    for (;;) {
        long long now = clock_ns(CLOCK_MONOTONIC);
        unsigned long long values[FIELDS];
        if (meminfo(values)) break;
        if (previous && now - previous > max_gap) max_gap = now - previous;
        previous = now;
        fprintf(table, "%lld\t%lld\t%lld", clock_ns(CLOCK_REALTIME), now, now-start);
        for (size_t i = 0; i < FIELDS; ++i) fprintf(table, "\t%llu", values[i]);
        fputc('\n', table);
        if (fflush(table)) break;
        if (++count == 1) {
            FILE *ready = fopen("ready.tmp", "wx");
            if (!ready) break;
            fprintf(ready, "{\"status\":\"STARTED\",\"pid\":%d}\n", getpid());
            if (fclose(ready) || rename("ready.tmp", "ready")) break;
        }
        if (!access("stop", F_OK)) {
            success = 1;
            break;
        }
        if (errno != ENOENT || now - start >= limit * 1000000000LL) break;
        struct timespec delay = {0, 100000000};
        while (nanosleep(&delay, &delay) && errno == EINTR) {}
    }
    if (fclose(table)) success = 0;
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage)) return 1;
    FILE *receipt = fopen("collector-status.json", "wx");
    if (!receipt) return 1;
    fprintf(receipt, "{\"status\":\"%s\",\"pid\":%d,\"sandbox_id\":\"%s\",\"session_id\":\"%s\","
            "\"samples\":%u,\"interval_seconds\":0.1,\"elapsed_ns\":%lld,\"max_sample_interval_ns\":%lld,"
            "\"cpu_user_seconds\":%.6f,\"cpu_system_seconds\":%.6f,\"maxrss_kib\":%ld}\n",
            success ? "PASS" : "FAIL", getpid(), argv[2], argv[3], count,
            clock_ns(CLOCK_MONOTONIC)-start, max_gap, seconds(usage.ru_utime),
            seconds(usage.ru_stime), usage.ru_maxrss);
    if (fclose(receipt)) return 1;
    return success ? 0 : 1;
}
