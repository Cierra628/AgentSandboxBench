/* Read-only capability probe on this process; never attaches to another workload. */
#include <errno.h>
#include <linux/perf_event.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/syscall.h>
#include <unistd.h>

int main(void) {
    struct perf_event_attr attr = {0};
    attr.type = PERF_TYPE_HARDWARE;
    attr.size = sizeof(attr);
    attr.config = PERF_COUNT_HW_CPU_CYCLES;
    attr.disabled = 1;
    attr.exclude_kernel = 1;
    attr.exclude_hv = 1;
    attr.read_format = PERF_FORMAT_GROUP | PERF_FORMAT_TOTAL_TIME_ENABLED | PERF_FORMAT_TOTAL_TIME_RUNNING;
    int cycles = syscall(__NR_perf_event_open, &attr, 0, -1, -1, 0);
    if (cycles < 0) { fprintf(stderr, "cycles: %s\n", strerror(errno)); return 1; }
    attr.config = PERF_COUNT_HW_INSTRUCTIONS;
    int instructions = syscall(__NR_perf_event_open, &attr, 0, -1, cycles, 0);
    if (instructions < 0) { fprintf(stderr, "instructions: %s\n", strerror(errno)); close(cycles); return 1; }
    int status = 1;
    if (ioctl(cycles, PERF_EVENT_IOC_RESET, PERF_IOC_FLAG_GROUP) ||
        ioctl(cycles, PERF_EVENT_IOC_ENABLE, PERF_IOC_FLAG_GROUP)) goto cleanup;
    volatile uint64_t value = 1;
    for (unsigned i = 0; i < 2000000; i++) value = value * 1664525 + 1013904223;
    if (ioctl(cycles, PERF_EVENT_IOC_DISABLE, PERF_IOC_FLAG_GROUP)) goto cleanup;
    struct { uint64_t count, enabled, running, cycles, instructions; } result;
    if (read(cycles, &result, sizeof(result)) != sizeof(result) || result.count != 2 ||
        !result.running || !result.cycles || !result.instructions) goto cleanup;
    printf("{\"status\":\"PASS\",\"scope\":\"own host process, user mode only; guest attribution NOT_VALIDATED\","
           "\"enabled_ns\":%llu,\"running_ns\":%llu,\"cycles\":%llu,\"instructions\":%llu}\n",
           (unsigned long long)result.enabled, (unsigned long long)result.running,
           (unsigned long long)result.cycles, (unsigned long long)result.instructions);
    status = 0;
cleanup:
    close(instructions); close(cycles);
    if (status) fprintf(stderr, "counter enable/read failed\n");
    return status;
}
