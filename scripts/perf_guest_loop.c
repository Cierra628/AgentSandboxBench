/* Exactly two loop instructions per iteration on x86_64, plus fixed setup/exit. */
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
int main(int argc,char **argv) {
    if(argc!=2)return 2;
    char *end; uint64_t n=strtoull(argv[1],&end,10);
    if(*end || n>2000000000ULL)return 2;
    if(n) __asm__ volatile("1: dec %0; jnz 1b" : "+r"(n) : : "cc");
    puts("LOOP_PASS");return 0;
}
