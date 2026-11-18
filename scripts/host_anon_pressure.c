/* Owned anonymous pressure only: touch each page once, then hold a fixed allocation. */
#define _GNU_SOURCE
#include <sys/mman.h>
#include <unistd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>

static volatile sig_atomic_t stopped;
static void stop_handler(int sig) { (void)sig; stopped=1; }
static uint64_t available(void) {
    FILE *f=fopen("/proc/meminfo","r");
    if (!f) { perror("meminfo"); exit(2); }
    char line[256]; unsigned long long value;
    while (fgets(line,sizeof(line),f)) {
        if (sscanf(line,"MemAvailable: %llu kB",&value)==1) { fclose(f); return (uint64_t)value*1024; }
    }
    fclose(f); fprintf(stderr,"MemAvailable missing\n"); exit(2);
}
int main(int argc,char **argv) {
    if (argc!=4) { fprintf(stderr,"usage: host_anon_pressure ready.json target_available_bytes max_allocated_bytes\n"); return 2; }
    char *tail; errno=0; uint64_t target=strtoull(argv[2],&tail,10);
    if (errno || *tail) return 2;
    uint64_t maximum=strtoull(argv[3],&tail,10);
    if (errno || *tail || !maximum) return 2;
    const uint64_t chunk=128ULL*1024*1024; long page=sysconf(_SC_PAGESIZE);
    size_t capacity=(size_t)(maximum/chunk+1),count=0; uint64_t allocated=0;
    void **blocks=calloc(capacity,sizeof(*blocks)); size_t *sizes=calloc(capacity,sizeof(*sizes));
    if (!blocks || !sizes || page<=0) return 2;
    signal(SIGTERM,stop_handler); signal(SIGINT,stop_handler);
    while (!stopped && allocated<maximum && available()>target) {
        size_t length=(size_t)(maximum-allocated<chunk ? maximum-allocated : chunk);
        void *p=mmap(NULL,length,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        if (p==MAP_FAILED) { perror("mmap"); stopped=1; break; }
        blocks[count]=p; sizes[count++]=length;
        volatile unsigned char *bytes=p;
        for (size_t offset=0;offset<length;offset+=(size_t)page) {
            if (stopped) break;
            bytes[offset]=1;
        }
        if (!stopped) allocated+=length;
    }
    uint64_t observed=available(); char temporary[4096];
    if (snprintf(temporary,sizeof(temporary),"%s.tmp",argv[1])>=(int)sizeof(temporary)) return 2;
    FILE *f=fopen(temporary,"wx");
    if (!f) { perror("ready file"); return 2; }
    fprintf(f,"{\"status\":\"%s\",\"pid\":%ld,\"allocated_bytes\":%llu,\"target_available_bytes\":%llu,\"available_bytes\":%llu,\"target_reached\":%s}\n",
        stopped?"ABORTED":"HOLDING",(long)getpid(),(unsigned long long)allocated,
        (unsigned long long)target,(unsigned long long)observed,observed<=target?"true":"false");
    if (fclose(f) || rename(temporary,argv[1])) { perror("ready write"); return 2; }
    sigset_t blocked,oldmask; sigemptyset(&blocked);sigaddset(&blocked,SIGTERM);sigaddset(&blocked,SIGINT);
    sigprocmask(SIG_BLOCK,&blocked,&oldmask);
    while (!stopped) sigsuspend(&oldmask);
    sigprocmask(SIG_SETMASK,&oldmask,NULL);
    for (size_t i=0;i<count;i++) munmap(blocks[i],sizes[i]);
    free(blocks);free(sizes);return 0;
}
