#include <wayland-client.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "im-protocol.h"
static struct wl_seat *seat;
static struct zwp_input_method_manager_v2 *manager;
static unsigned serial;
static void noop(void *d,struct zwp_input_method_v2 *im) {}
static void text(void*d,struct zwp_input_method_v2*i,const char*t,unsigned c,unsigned a) {}
static void cause(void*d,struct zwp_input_method_v2*i,unsigned c) {}
static void content(void*d,struct zwp_input_method_v2*i,unsigned h,unsigned p) {}
static void done(void*d,struct zwp_input_method_v2*i) {
 serial++; if(getenv("ACK")) zwp_input_method_v2_commit(i,serial);
}
static const struct zwp_input_method_v2_listener listener={noop,noop,text,cause,content,done,noop};
static void global(void*d,struct wl_registry*r,unsigned n,const char*i,unsigned v) {
 if(!strcmp(i,"wl_seat")) seat=wl_registry_bind(r,n,&wl_seat_interface,1);
 if(!strcmp(i,"zwp_input_method_manager_v2")) manager=wl_registry_bind(r,n,&zwp_input_method_manager_v2_interface,1);
}
static void removed(void*d,struct wl_registry*r,unsigned n) {}
int main(void) {
 struct wl_display *d=wl_display_connect(NULL); if(!d)return 1;
 struct wl_registry*r=wl_display_get_registry(d);
 const struct wl_registry_listener l={global,removed}; wl_registry_add_listener(r,&l,NULL);
 wl_display_roundtrip(d); if(!seat||!manager)return 2;
 struct zwp_input_method_v2*im=zwp_input_method_manager_v2_get_input_method(manager,seat);
 zwp_input_method_v2_add_listener(im,&listener,NULL);
 while(wl_display_dispatch(d)>=0){} return 0;
}
