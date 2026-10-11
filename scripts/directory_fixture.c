/* Known directory traversal plus missing path, EOF and errno checks. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static uint64_t hash(const char *s) {
    uint64_t h=1469598103934665603ULL;
    for (;*s;++s) {h^=(unsigned char)*s;h*=1099511628211ULL;} return h;
}
static int number(const char *s) {
    int value=0;
    if (!*s) return -1;
    for (;*s;++s) {
        if (*s<'0' || *s>'9' || value>1000) return -1;
        value=value*10+(*s-'0');
    }
    return value;
}
int main(int argc,char **argv) {
    if (argc!=4 || chdir(argv[1])) return 2;
    /* Avoid the host's C23 strtol symbol, absent from the fixed guest glibc. */
    int rounds=number(argv[2]), exit_code=number(argv[3]);
    if (rounds<1 || rounds>1000 || exit_code<0 || exit_code>255) return 2;
    uint64_t count=0,sum=0;
    for (int r=0;r<rounds;++r) for (int i=0;i<256;++i) {
        char path[32]; snprintf(path,sizeof(path),"d%03d",i);
        DIR *d=opendir(path); if (!d) return 3;
        for (;;) {
            errno=0;
            if (r%2) {
                struct dirent64 *p=readdir64(d);
                if (!p) {if(errno) return 4;break;}
                if (p->d_name[0]!='.') {++count;sum+=hash(p->d_name);}
            } else {
                struct dirent *p=readdir(d);
                if (!p) {if(errno) return 4;break;}
                if (p->d_name[0]!='.') {++count;sum+=hash(p->d_name);}
            }
        }
        errno=EDOM;
        if (readdir(d)!=NULL || errno!=EDOM) return 5;
        if (closedir(d)) return 6;
    }
    errno=0;
    if (opendir("missing-directory")!=NULL || errno!=ENOENT) return 7;
    printf("{\"entries\":%llu,\"name_hash_sum\":%llu,\"errno_checks\":\"PASS\"}\n",
           (unsigned long long)count,(unsigned long long)sum);
    return exit_code;
}
