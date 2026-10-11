/* Fixed operation count, deterministic unsigned arithmetic; no sleep or timer loop. */
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc != 2) return 2;
    char *end; errno=0;
    uint64_t iterations=strtoull(argv[1],&end,10);
    if (errno || *end || !iterations || iterations>UINT64_C(2000000000) || argv[1][0]=='-') return 2;
    volatile uint64_t state=UINT64_C(0x123456789abcdef0);
    for (uint64_t i=0;i<iterations;i++) {
        state=state*UINT64_C(6364136223846793005)+UINT64_C(1442695040888963407);
        state=state^(state>>13);
    }
    printf("iterations=%" PRIu64 " checksum=%016" PRIx64 "\n",iterations,(uint64_t)state);
    return 0;
}
