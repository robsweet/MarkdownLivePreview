"""
Terminology
original_view: the view in the regular editor, without it's own window
markdown_view: the markdown view, in the special window
preview_view: the preview view, in the special window
original_window: the regular window
preview_window: the window with the markdown file and the preview
"""
import os.path
import time
from functools import partial

import sublime
import sublime_plugin

from .markdown2html import markdown2html

PREVIEW_VIEW_INFO = "preview_view_info"
resources = {}


def find_preview(view):
    """find previews for input view."""
    view_id = view.id()
    for x in view.window().views():
        d = x.settings().get(PREVIEW_VIEW_INFO)
        if d and d.get("id") == view_id:
            yield x


def get_resource(resource):
    path = "Packages/MarkdownLivePreview/resources/" + resource
    abs_path = os.path.join(sublime.packages_path(), "..", path)
    if os.path.isfile(abs_path):
        with open(abs_path, "r") as fp:
            return fp.read()
    return sublime.load_resource(path)


class MarkdownLivePreviewListener(sublime_plugin.EventListener):
    last_update = 0  # update only if now() - last_update > DELAY
    phantom_sets = {}  # {preview.id(): PhantomSet}

    def on_pre_close(self, view):
        """Closing markdown files closes any associated previews."""
        if "markdown" in view.settings().get("syntax").lower():
            previews = list(find_preview(view))
            if previews:
                window = view.window()
                for preview in previews:
                    window.focus_view(preview)
                    window.run_command("close_file")
        else:
            d = view.settings().get(PREVIEW_VIEW_INFO)
            if d:
                view_id = view.id()
                if view_id in self.phantom_sets:
                    del self.phantom_sets[view_id]

    def on_modified_async(self, view):
        """Schedule an update when changing markdown files"""
        if "markdown" in view.settings().get("syntax").lower():
            sublime.set_timeout(partial(self.update_preview, view), DELAY)

    def update_preview(self, view):
        # if the buffer id is 0, that means that the markdown_view has been
        # closed. This check is needed since a this function is used as a
        # callback for when images are loaded from the internet (ie. it could
        # finish loading *after* the user closes the markdown_view)
        if time.time() - self.last_update < DELAY / 1000:
            return
        if view.buffer_id() == 0:
            return
        previews = list(find_preview(view))
        if not previews:
            return
        self.last_update = time.time()
        for preview in previews:
            html = markdown2html(
                view.substr(sublime.Region(0, view.size())),
                os.path.dirname(view.file_name()),
                partial(self.update_preview, view),
                resources,
                preview.viewport_extent()[0],
            )
            self.phantom_sets[preview.id()].update(
                [sublime.Phantom(sublime.Region(0), html, sublime.LAYOUT_BLOCK, lambda x: sublime.run_command("open_url", {"url": x}))]
            )


class OpenMarkdownPreviewCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        """Set to multi-pane layout. Open markdown preview in another pane."""
        window = sublime.active_window()
        if window.num_groups() < 2:
            window.set_layout({"cols": [0.0, 0.5, 1.0], "rows": [0.0, 1.0], "cells": [[0, 0, 1, 1], [1, 0, 2, 1]]})
        window.focus_group(0 if window.active_group() else 1)
        view = window.new_file()
        view.set_scratch(True)
        view.set_name("Preview")
        view.settings().set(PREVIEW_VIEW_INFO, {"id": self.view.id()})
        ps = MarkdownLivePreviewListener.phantom_sets
        ps[view.id()] = sublime.PhantomSet(view)
        MarkdownLivePreviewListener().update_preview(self.view)
        window.focus_view(self.view)

    def is_enabled(self):
        return "markdown" in self.view.settings().get("syntax").lower()


def parse_image_resource(text):
    width, height, base64_image = text.splitlines()
    return base64_image, (int(width), int(height))


def plugin_loaded():
    global DELAY
    DELAY = sublime.load_settings("MarkdownLivePreview.sublime-settings").get("delay_between_updates")
    resources["base64_404_image"] = parse_image_resource(get_resource("404.base64"))
    resources["base64_loading_image"] = parse_image_resource(get_resource("loading.base64"))
    resources["stylesheet"] = get_resource("stylesheet.css")
