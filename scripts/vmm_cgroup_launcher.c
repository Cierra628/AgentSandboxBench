/* Explicit validation launcher. No migration or fork fallback: clone3 must
 * create the VMM in its final memory cgroup before exec of the original binary. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>

static volatile sig_atomic_t child_pid;
static void forward(int sig) { if (child_pid > 0) kill(child_pid, sig); }
static void fail(const char *step) { perror(step); exit(125); }

int main(int argc, char **argv) {
    const char *test = getenv("ASB_VMM_CLONE_TEST_CGROUP");
    const char *real = test ? "/bin/true" : "/workspace/env/deps/firecracker/1.15.1-patch-v1/firecracker";
    char group[4096], name[64], record[4096], membership[4096];
    int parent = -1, target = -1;
    prctl(PR_SET_NAME, "vmm-launcher", 0, 0, 0);
    int is_vm = 0;
    for (int i = 1; i < argc; i++) if (!strcmp(argv[i], "--api-sock")) is_vm = 1;
    if (!test && !is_vm) { argv[0] = (char *)real; execv(real, argv); fail("exec Firecracker utility"); }
    if (test) {
        if (snprintf(group, sizeof group, "%s", test) >= sizeof group) return 125;
    } else {
        FILE *f = fopen("/proc/self/cgroup", "r");
        if (!f || !fgets(membership, sizeof membership, f)) fail("read cgroup");
        fclose(f);
        const char *configured = getenv("ASB_VMM_CGROUP_PARENT");
        if (!configured || strncmp(configured, "/sys/fs/cgroup/", 15)) { errno = EINVAL; fail("missing experiment parent"); }
        const char *relative = configured + strlen("/sys/fs/cgroup");
        if (strncmp(membership, "0::", 3) || strncmp(membership+3, relative, strlen(relative)) ||
            membership[3+strlen(relative)] != '/') { errno = EINVAL; fail("launcher outside experiment parent"); }
        if (snprintf(group, sizeof group, "%s", configured) >= sizeof group) return 125;
        parent = open(group, O_DIRECTORY | O_RDONLY | O_CLOEXEC);
        if (parent < 0) fail("open service cgroup");
        snprintf(name, sizeof name, "vmm-%ld", (long)getpid());
        if (mkdirat(parent, name, 0755)) fail("mkdir VMM cgroup");
        target = openat(parent, name, O_DIRECTORY | O_RDONLY | O_CLOEXEC);
        close(parent);
        if (target < 0) fail("open VMM cgroup");
    }
    if (test) target = open(group, O_DIRECTORY | O_RDONLY | O_CLOEXEC);
    if (target < 0) fail("open test cgroup");
    struct clone_args args = { .flags = CLONE_INTO_CGROUP, .exit_signal = SIGCHLD, .cgroup = target };
    pid_t launcher = getpid();
    pid_t pid = syscall(SYS_clone3, &args, sizeof args);
    if (pid < 0) fail("clone3 CLONE_INTO_CGROUP");
    if (pid == 0) {
        close(target);
        if (prctl(PR_SET_PDEATHSIG, SIGKILL)) fail("parent death signal");
        if (getppid() != launcher) _exit(125);
        FILE *f = fopen("/proc/self/cgroup", "r");
        if (!f || !fgets(membership, sizeof membership, f)) fail("read birth cgroup");
        fclose(f); membership[strcspn(membership, "\n")] = 0;
        if (test) {
            printf("birth=%s\n", membership); fflush(stdout);
            char *command[] = { "/bin/true", NULL }; execv(real, command);
        } else {
            snprintf(record, sizeof record, "/workspace/env/logs/vmm-cgroups/%s.json", name);
            int fd = open(record, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
            if (fd < 0) fail("open birth record");
            dprintf(fd, "{\"birth_cgroup\":\"%s\",\"namespace_pid\":%ld,\"launcher_pid\":%ld,\"creation\":\"clone3_CLONE_INTO_CGROUP\"}\n",
                    membership, (long)getpid(), (long)launcher);
            close(fd); argv[0] = (char *)real; execv(real, argv);
        }
        fail("exec original Firecracker");
    }
    close(target); child_pid = pid;
    signal(SIGTERM, forward); signal(SIGINT, forward); signal(SIGHUP, forward);
    int status;
    while (waitpid(pid, &status, 0) < 0) if (errno != EINTR) fail("wait VMM");
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
