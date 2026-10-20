/* Guest-local fixed 320x240 BGRA camera stream. No device or host IPC access. */
#include <signal.h>
#include <stdint.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <pipewire/pipewire.h>
#include <spa/param/video/format-utils.h>
struct camera {
    struct pw_main_loop *loop;
    struct pw_stream *stream;
    struct spa_source *timer;
    struct spa_video_info_raw format;
    size_t input_used;
    unsigned char incoming[320*240*4];
    unsigned char latest[320*240*4];
};
static void input_ready(void *arg, int fd, uint32_t mask) {
    struct camera *c=arg;
    (void)mask;
    /* Bound work per callback as well as allocations; no guest-controlled size. */
    for (unsigned i=0; i<8; i++) {
        ssize_t n=read(fd,c->incoming+c->input_used,sizeof(c->incoming)-c->input_used);
        if (n==0) { pw_main_loop_quit(c->loop); return; }
        if (n<0) {
            if (errno==EAGAIN || errno==EINTR) return;
            pw_main_loop_quit(c->loop); return;
        }
        c->input_used+=(size_t)n;
        if (c->input_used==sizeof(c->incoming)) {
            memcpy(c->latest,c->incoming,sizeof(c->latest)); c->input_used=0;
        }
    }
}
static void process(void *arg) {
    struct camera *c = arg;
    struct pw_buffer *b = pw_stream_dequeue_buffer(c->stream);
    if (!b) return;
    struct spa_data *d = &b->buffer->datas[0];
    uint32_t stride = c->format.size.width * 4;
    uint32_t size = stride * c->format.size.height;
    if (d->data && size <= d->maxsize) {
        unsigned char *pixels = d->data;
        memcpy(pixels,c->latest,size);
        d->chunk->offset=0; d->chunk->size=size; d->chunk->stride=stride;
    } else d->chunk->size=0;
    pw_stream_queue_buffer(c->stream,b);
}
static void format(void *arg, uint32_t id, const struct spa_pod *param) {
    struct camera *c=arg;
    if (id != SPA_PARAM_Format || !param) return;
    if (spa_format_video_raw_parse(param,&c->format)<0 ||
        c->format.size.width!=320 || c->format.size.height!=240 ||
        c->format.format!=SPA_VIDEO_FORMAT_BGRA) {
        memset(&c->format,0,sizeof(c->format)); pw_main_loop_quit(c->loop); return;
    }
    uint8_t storage[1024]; struct spa_pod_builder b=SPA_POD_BUILDER_INIT(storage,sizeof(storage));
    const struct spa_pod *params[1];
    params[0]=spa_pod_builder_add_object(&b,SPA_TYPE_OBJECT_ParamBuffers,SPA_PARAM_Buffers,
        SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(4,2,8),
        SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(1),
        SPA_PARAM_BUFFERS_size, SPA_POD_Int(c->format.size.width*c->format.size.height*4),
        SPA_PARAM_BUFFERS_stride, SPA_POD_Int(c->format.size.width*4));
    pw_stream_update_params(c->stream,params,1);
}
static void tick(void *arg,uint64_t n) { (void)n; struct camera *c=arg; pw_stream_trigger_process(c->stream); }
static void state(void *arg,enum pw_stream_state old,enum pw_stream_state next,const char *error) {
    (void)old; (void)error; struct camera *c=arg;
    struct timespec value={0,1}, interval={0,40000000};
    if (next!=PW_STREAM_STATE_STREAMING) value.tv_nsec=interval.tv_nsec=0;
    pw_loop_update_timer(pw_main_loop_get_loop(c->loop),c->timer,&value,&interval,false);
}
static void stop(void *arg,int signum) { (void)signum; pw_main_loop_quit(((struct camera *)arg)->loop); }
int main(int argc,char **argv) {
    struct camera c={0};
    if (argc!=2 || strcmp(argv[1],"--stdin")) return 2;
    pw_init(&argc,&argv);
    c.loop=pw_main_loop_new(NULL);
    {
        int flags=fcntl(STDIN_FILENO,F_GETFL);
        if (flags<0 || fcntl(STDIN_FILENO,F_SETFL,flags|O_NONBLOCK)<0) return 1;
        if (!pw_loop_add_io(pw_main_loop_get_loop(c.loop),STDIN_FILENO,SPA_IO_IN|SPA_IO_HUP,true,input_ready,&c)) return 1;
    }
    c.timer=pw_loop_add_timer(pw_main_loop_get_loop(c.loop),tick,&c);
    pw_loop_add_signal(pw_main_loop_get_loop(c.loop),SIGTERM,stop,&c);
    pw_loop_add_signal(pw_main_loop_get_loop(c.loop),SIGINT,stop,&c);
    static const struct pw_stream_events events={PW_VERSION_STREAM_EVENTS,.state_changed=state,.param_changed=format,.process=process};
    c.stream=pw_stream_new_simple(pw_main_loop_get_loop(c.loop),"mbox-camera",
        pw_properties_new(PW_KEY_MEDIA_CLASS,"Video/Source",PW_KEY_MEDIA_ROLE,"Camera",
            PW_KEY_NODE_DESCRIPTION,"Mbox camera",PW_KEY_NODE_NAME,"mbox-camera",NULL),&events,&c);
    uint8_t storage[1024]; struct spa_pod_builder b=SPA_POD_BUILDER_INIT(storage,sizeof(storage));
    const struct spa_pod *params[1];
    params[0]=spa_pod_builder_add_object(&b,SPA_TYPE_OBJECT_Format,SPA_PARAM_EnumFormat,
        SPA_FORMAT_mediaType,SPA_POD_Id(SPA_MEDIA_TYPE_video),
        SPA_FORMAT_mediaSubtype,SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
        SPA_FORMAT_VIDEO_format,SPA_POD_Id(SPA_VIDEO_FORMAT_BGRA),
        SPA_FORMAT_VIDEO_size,SPA_POD_Rectangle(&SPA_RECTANGLE(320,240)),
        SPA_FORMAT_VIDEO_framerate,SPA_POD_Fraction(&SPA_FRACTION(25,1)));
    if (pw_stream_connect(c.stream,PW_DIRECTION_OUTPUT,PW_ID_ANY,PW_STREAM_FLAG_DRIVER|PW_STREAM_FLAG_MAP_BUFFERS,params,1)<0) return 1;
    pw_main_loop_run(c.loop);
    pw_stream_destroy(c.stream); pw_main_loop_destroy(c.loop); pw_deinit(); return 0;
}
