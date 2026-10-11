/* Isolated guest reader: distinguish read-only mmap from private writable mmap. */
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    int writable = strcmp(argv[1], "read") != 0;
    int fd = open(argv[2], O_RDONLY);
    struct stat st;
    if (fd < 0 || fstat(fd, &st) || st.st_size <= 0) return 3;
    volatile unsigned char *data = mmap(NULL, st.st_size,
        PROT_READ | (writable ? PROT_WRITE : 0), MAP_PRIVATE, fd, 0);
    if (data == MAP_FAILED) return 4;
    unsigned long sum = 0;
    for (off_t i = 0; i < st.st_size; i++) sum += data[i];
    if (!strcmp(argv[1], "write")) data[0] ^= 1;
    printf("sum=%lu private_write=%d\n", sum, !strcmp(argv[1], "write"));
    if (munmap((void *)data, st.st_size) || close(fd)) return 5;
    return 0;
}
