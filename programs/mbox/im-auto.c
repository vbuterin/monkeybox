/* GTK3 IM selector for the nested guest: native Wayland reports caret geometry,
 * X11 uses the private XIM server. Do not force either backend on the other. */
#include <gtk/gtk.h>
#include <gtk/gtkimmodule.h>
#include <gdk/gdkwayland.h>
static const GtkIMContextInfo info = { "mbox", "Monkeybox", "gtk30", "", "" };
G_MODULE_EXPORT void im_module_init(GTypeModule *module) {}
G_MODULE_EXPORT void im_module_exit(void) {}
G_MODULE_EXPORT void im_module_list(const GtkIMContextInfo ***contexts, int *count) {
    static const GtkIMContextInfo *items[] = { &info };
    *contexts = items;
    *count = 1;
}
G_MODULE_EXPORT GtkIMContext *im_module_create(const gchar *id) {
    if (g_strcmp0(id, "mbox") != 0) return NULL;
    /* set_context_id first resets the old default context. That reset can
     * reenter this factory before the explicit backend ID is installed. */
    static _Thread_local gboolean selecting;
    if (selecting) return gtk_im_context_simple_new();
    selecting = TRUE;
    GtkIMContext *context = gtk_im_multicontext_new();
    gtk_im_multicontext_set_context_id(GTK_IM_MULTICONTEXT(context),
        GDK_IS_WAYLAND_DISPLAY(gdk_display_get_default()) ? "mbox-wayland" : "xim");
    selecting = FALSE;
    return context;
}
