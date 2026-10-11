/* Directory libc call timing. No paths or entries are saved; one file per exit.
 * Normal exit only: _exit, signals and static binaries are not covered.
 * Durations are inclusive libc time, not syscall time. Compile with -ldl -pthread.
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

#ifdef DIRECTORY_OPEN_ONLY
#define DIRECTORY_OPEN_CLOSE_ONLY
enum { OPEN, N };
static const char *names[N] = {"opendir"};
#elif defined(DIRECTORY_OPEN_CLOSE_ONLY)
enum { OPEN, CLOSE, N };
static const char *names[N] = {"opendir", "closedir"};
#else
enum { OPEN, READ, READ64, CLOSE, N };
static const char *names[N] = {"opendir", "readdir", "readdir64", "closedir"};
#endif
static struct { _Atomic uint64_t count, total, maximum, errors, eof; } stats[N];
static DIR *(*real_open)(const char *);
#ifndef DIRECTORY_OPEN_CLOSE_ONLY
static struct dirent *(*real_read)(DIR *);
static struct dirent64 *(*real_read64)(DIR *);
#endif
#ifndef DIRECTORY_OPEN_ONLY
static int (*real_close)(DIR *);
#endif
static const char *output;
static uint64_t now(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t)) _exit(124);
    return (uint64_t)t.tv_sec * 1000000000 + t.tv_nsec;
}
static void reset(void) {
    for (int i=0; i<N; ++i) {
        atomic_store(&stats[i].count,0); atomic_store(&stats[i].total,0);
        atomic_store(&stats[i].maximum,0); atomic_store(&stats[i].errors,0);
        atomic_store(&stats[i].eof,0);
    }
}
__attribute__((constructor)) static void init(void) {
    real_open=dlsym(RTLD_NEXT,"opendir");
    if (!real_open) _exit(125);
#ifndef DIRECTORY_OPEN_ONLY
    real_close=dlsym(RTLD_NEXT,"closedir");
    if (!real_close) _exit(125);
#endif
#ifndef DIRECTORY_OPEN_CLOSE_ONLY
    real_read=dlsym(RTLD_NEXT,"readdir"); real_read64=dlsym(RTLD_NEXT,"readdir64");
    if (!real_read || !real_read64) _exit(125);
#endif
    output=getenv("DIRECTORY_PROFILE_DIR");
    if (pthread_atfork(NULL,NULL,reset)) _exit(126);
}
static void record(int kind, uint64_t start, int error, int eof) {
    uint64_t delta=now()-start;
    atomic_fetch_add_explicit(&stats[kind].count,1,memory_order_relaxed);
    atomic_fetch_add_explicit(&stats[kind].total,delta,memory_order_relaxed);
    if (error) atomic_fetch_add_explicit(&stats[kind].errors,1,memory_order_relaxed);
    if (eof) atomic_fetch_add_explicit(&stats[kind].eof,1,memory_order_relaxed);
    uint64_t old=atomic_load_explicit(&stats[kind].maximum,memory_order_relaxed);
    while (old<delta && !atomic_compare_exchange_weak_explicit(&stats[kind].maximum,
           &old,delta,memory_order_relaxed,memory_order_relaxed)) {}
}
DIR *opendir(const char *path) {
    if (!output) return real_open(path);
    int saved=errno; uint64_t start=now(); errno=saved;
    DIR *p=real_open(path); saved=errno;
    record(OPEN,start,p==NULL,0); errno=saved; return p;
}
#ifndef DIRECTORY_OPEN_CLOSE_ONLY
struct dirent *readdir(DIR *d) {
    if (!output) return real_read(d);
    int saved=errno; uint64_t start=now(); errno=saved;
    struct dirent *p=real_read(d); saved=errno;
    /* EOF classification requires callers to clear errno before readdir. */
    record(READ,start,!p && saved!=0,!p && saved==0); errno=saved; return p;
}
struct dirent64 *readdir64(DIR *d) {
    if (!output) return real_read64(d);
    int saved=errno; uint64_t start=now(); errno=saved;
    struct dirent64 *p=real_read64(d); saved=errno;
    record(READ64,start,!p && saved!=0,!p && saved==0); errno=saved; return p;
}
#endif
#ifndef DIRECTORY_OPEN_ONLY
int closedir(DIR *d) {
    if (!output) return real_close(d);
    int saved=errno; uint64_t start=now(); errno=saved;
    int rc=real_close(d); saved=errno;
    record(CLOSE,start,rc!=0,0); errno=saved; return rc;
}
#endif
__attribute__((destructor)) static void finish(void) {
    if (!output) return;
    uint64_t count=0;
    for (int i=0;i<N;++i) count+=atomic_load(&stats[i].count);
    if (!count) return;
    char path[4096];
    int len=snprintf(path,sizeof(path),"%s/directory-%ld.json",output,(long)getpid());
    if (len<0 || (size_t)len>=sizeof(path)) return;
    int fd=open(path,O_WRONLY|O_CREAT|O_EXCL|O_CLOEXEC,0600);
    if (fd<0) return; /* Missing output is rejected by the external checker. */
    dprintf(fd,"{\"schema\":1,\"pid\":%ld,\"calls\":{",(long)getpid());
    for (int i=0;i<N;++i) dprintf(fd,
        "%s\"%s\":{\"count\":%llu,\"total_ns\":%llu,\"max_ns\":%llu,"
        "\"null_with_errno\":%llu,\"null_without_errno\":%llu}",i?",":"",names[i],
        (unsigned long long)atomic_load(&stats[i].count),
        (unsigned long long)atomic_load(&stats[i].total),
        (unsigned long long)atomic_load(&stats[i].maximum),
        (unsigned long long)atomic_load(&stats[i].errors),
        (unsigned long long)atomic_load(&stats[i].eof));
    dprintf(fd,"}}\n"); close(fd);
}
