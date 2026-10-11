/* Explicit TIDs only. Caller must verify sandbox ownership before attaching. */
#include <linux/perf_event.h>
#include <sys/syscall.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
struct counters { uint64_t nr, enabled, running, cycles, instructions, reference_cycles; };
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) return 2;
    alarm(30);
    int fd[2][2][3]; memset(fd,-1,sizeof(fd));
    int rc=1, n=argc-1;
    for(int t=0;t<n;t++) for(int s=0;s<2;s++) {
        char *end; long tid=strtol(argv[t+1],&end,10);
        if(*end || tid<=0) goto done;
        struct perf_event_attr a={0};
        a.type=PERF_TYPE_HARDWARE; a.size=sizeof(a); a.disabled=1;
        a.exclude_host=s==0; a.exclude_guest=s==1;
        a.read_format=PERF_FORMAT_GROUP|PERF_FORMAT_TOTAL_TIME_ENABLED|PERF_FORMAT_TOTAL_TIME_RUNNING;
        a.config=PERF_COUNT_HW_CPU_CYCLES;
        fd[t][s][0]=syscall(__NR_perf_event_open,&a,tid,-1,-1,0);
        if(fd[t][s][0]<0) goto done;
        a.config=PERF_COUNT_HW_INSTRUCTIONS;
        fd[t][s][1]=syscall(__NR_perf_event_open,&a,tid,-1,fd[t][s][0],0);
        if(fd[t][s][1]<0) goto done;
        if(s==0) {
            a.config=PERF_COUNT_HW_REF_CPU_CYCLES;
            fd[t][s][2]=syscall(__NR_perf_event_open,&a,tid,-1,fd[t][s][0],0);
            if(fd[t][s][2]<0) goto done;
        }
        if(ioctl(fd[t][s][0],PERF_EVENT_IOC_ENABLE,PERF_IOC_FLAG_GROUP)) goto done;
    }
    puts("READY"); fflush(stdout);
    if(getchar()!='S') goto done;
    for(int t=0;t<n;t++) for(int s=0;s<2;s++)
        if(ioctl(fd[t][s][0],PERF_EVENT_IOC_DISABLE,PERF_IOC_FLAG_GROUP)) goto done;
    puts("[");
    for(int t=0;t<n;t++) for(int s=0;s<2;s++) {
        struct counters r={0};
        size_t bytes=sizeof(r)-(s?sizeof(uint64_t):0);
        if(read(fd[t][s][0],&r,bytes)!=(ssize_t)bytes || r.nr!=(s?2:3)) goto done;
        printf("%s{\"tid\":%s,\"scope\":\"%s\",\"enabled_ns\":%llu,\"running_ns\":%llu,\"cycles\":%llu,\"instructions\":%llu,\"reference_cycles\":%llu}",
            t||s?",\n":"",argv[t+1],s?"host":"guest",(unsigned long long)r.enabled,
            (unsigned long long)r.running,(unsigned long long)r.cycles,(unsigned long long)r.instructions,(unsigned long long)r.reference_cycles);
    }
    puts("\n]"); rc=0;
done:
    if(rc) fprintf(stderr,"counter failure: %s\n",strerror(errno));
    for(int t=0;t<2;t++)for(int s=0;s<2;s++)for(int e=0;e<3;e++)if(fd[t][s][e]>=0)close(fd[t][s][e]);
    return rc;
}
