/* Metadata only: the sole submitted command is UBLK_U_CMD_GET_DEV_INFO. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <liburing.h>
#include <linux/ublk_cmd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

_Static_assert(sizeof(struct ublksrv_ctrl_cmd) == 32, "control ABI");
_Static_assert(sizeof(struct ublksrv_io_desc) == 24, "descriptor ABI");

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "--abi")) {
        printf("{\"descriptor_bytes\":%zu,\"control_bytes\":%zu}\n",
               sizeof(struct ublksrv_io_desc), sizeof(struct ublksrv_ctrl_cmd));
        return 0;  /* No ring, device or service access. */
    }
    if (argc != 3) {
        fprintf(stderr, "usage: host-ublk-info CHAR_PATH DEV_ID | --abi\n");
        return 2;
    }
    char *end;
    errno = 0;
    unsigned long id = strtoul(argv[2], &end, 10);
    if (errno || !*argv[2] || *end || id > UINT32_MAX) return 2;
    /* O_PATH does not invoke ublk_ch_open (exclusive daemon ownership). */
    int fd = open(argv[1], O_PATH | O_CLOEXEC);
    struct stat st;
    if (fd < 0 || fstat(fd, &st)) { perror("char device"); return 1; }
    if (!S_ISCHR(st.st_mode) || minor(st.st_rdev) != id) {
        fprintf(stderr, "char device identity differs\n"); close(fd); return 1;
    }
    int control = open("/dev/ublk-control", O_RDWR | O_CLOEXEC);
    if (control < 0) { perror("control"); close(fd); return 1; }
    struct io_uring ring;
    int ret = io_uring_queue_init(2, &ring, IORING_SETUP_SQE128);
    if (ret < 0) {
        fprintf(stderr, "ring: %s\n", strerror(-ret));
        close(control); close(fd); return 1;
    }
    struct ublksrv_ctrl_dev_info info = {0};
    struct ublksrv_ctrl_cmd cmd = {
        .dev_id = id, .queue_id = (__u16)-1,
        .len = sizeof(info), .addr = (uintptr_t)&info,
    };
    struct io_uring_sqe *sqe = io_uring_get_sqe(&ring);
    io_uring_prep_rw(IORING_OP_URING_CMD, sqe, control, NULL, 0, 0);
    sqe->cmd_op = UBLK_U_CMD_GET_DEV_INFO;
    memcpy(sqe->cmd, &cmd, sizeof(cmd));
    ret = io_uring_submit(&ring);
    if (ret == 1) {
        struct io_uring_cqe *cqe;
        struct __kernel_timespec timeout = {.tv_sec = 2};
        ret = io_uring_wait_cqe_timeout(&ring, &cqe, &timeout);
        if (!ret) { ret = cqe->res; io_uring_cqe_seen(&ring, cqe); }
    } else if (ret >= 0) ret = -EIO;
    if (ret || info.dev_id != id) {
        fprintf(stderr, "GET_DEV_INFO: %s\n", ret < 0 ? strerror(-ret) : "identity/result differs");
    } else {
        printf("{\"device\":%ju,\"inode\":%ju,\"rdev\":%ju,\"dev_id\":%u,"
               "\"nr_hw_queues\":%u,\"queue_depth\":%u,\"state\":%u,\"ublksrv_pid\":%d,"
               "\"descriptor_bytes\":%zu,\"max_queue_depth\":%u,"
               "\"command_buffer_offset\":%u,\"page_size\":%ld}\n",
               (uintmax_t)st.st_dev, (uintmax_t)st.st_ino, (uintmax_t)st.st_rdev,
               info.dev_id, info.nr_hw_queues, info.queue_depth, info.state, info.ublksrv_pid,
               sizeof(struct ublksrv_io_desc), UBLK_MAX_QUEUE_DEPTH,
               UBLKSRV_CMD_BUF_OFFSET, sysconf(_SC_PAGESIZE));
    }
    io_uring_queue_exit(&ring);
    close(control); close(fd);
    return ret || info.dev_id != id ? 1 : 0;
}
