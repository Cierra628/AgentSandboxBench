#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

static int64_t clock_ns(clockid_t id) {
    struct timespec ts;
    if (clock_gettime(id, &ts)) { perror("clock_gettime"); exit(125); }
    return (int64_t)ts.tv_sec * 1000000000 + ts.tv_nsec;
}
static int64_t cpu_ns(struct timeval value) {
    return (int64_t)value.tv_sec * 1000000000 + value.tv_usec * 1000;
}
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: command-clock OUTPUT COMMAND [ARGS...]\n"); return 125; }
    int64_t real_start=clock_ns(CLOCK_REALTIME), mono_start=clock_ns(CLOCK_MONOTONIC_RAW);
    pid_t pid=fork();
    if (pid < 0) { perror("fork"); return 125; }
    if (!pid) { execvp(argv[2], argv+2); perror("execvp"); _exit(127); }
    int status; struct rusage usage;
    while (wait4(pid, &status, 0, &usage) < 0) { if (errno != EINTR) { perror("wait4"); return 125; } }
    int64_t mono_end=clock_ns(CLOCK_MONOTONIC_RAW), real_end=clock_ns(CLOCK_REALTIME);
    int code=WIFEXITED(status) ? WEXITSTATUS(status) : 128+WTERMSIG(status);
    FILE *out=fopen(argv[1],"w");
    if (!out) { perror("output"); return 125; }
    fprintf(out,"{\"realtime_ns\":%" PRId64 ",\"monotonic_raw_ns\":%" PRId64 ",\"user_ns\":%" PRId64 ",\"system_ns\":%" PRId64 ",\"minor_faults\":%ld,\"major_faults\":%ld,\"voluntary_switches\":%ld,\"involuntary_switches\":%ld,\"exit_code\":%d}\n",real_end-real_start,mono_end-mono_start,cpu_ns(usage.ru_utime),cpu_ns(usage.ru_stime),usage.ru_minflt,usage.ru_majflt,usage.ru_nvcsw,usage.ru_nivcsw,code);
    if (fclose(out)) { perror("output close"); return 125; }
    return code;
}
