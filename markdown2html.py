import base64
import io
import os.path
import struct
import urllib.request
from functools import partial

import bs4
import concurrent.futures

from .lib.markdown2 import Markdown

markdowner = Markdown(extras=["code-friendly", "fenced-code-blocks", "cuddled-lists"])
executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)


def markdown2html(markdown, basepath, re_render, resources, viewport_width):
    """converts the markdown to html.

    Loads the images and puts in base64 for sublime to understand them
    correctly. That means that we are responsible for loading the images from
    the internet. Hence, we take in re_render, which is just a function we call
    when an image has finished loading to retrigger a render (see #90)
    """
    html = markdowner.convert(markdown)

    soup = bs4.BeautifulSoup(html, "html.parser")
    for img_element in soup.find_all("img"):
        src = img_element["src"]

        # already in base64, or something of the like
        if src.startswith("data:image/"):
            continue

        if src.startswith("http://") or src.startswith("https://"):
            path = src
        elif src.startswith("file://"):
            path = src[len("file://") :]
        else:
            path = os.path.realpath(os.path.expanduser(os.path.join(basepath, src)))

        base64, (width, height) = get_base64_image(path, re_render, resources)

        img_element["src"] = base64
        if width > viewport_width:
            img_element["width"] = viewport_width
            img_element["height"] = viewport_width * (height / width)

    # remove comments, because they pollute the console with error messages
    [x.extract() for x in soup.find_all(text=lambda text: isinstance(text, bs4.Comment))]

    # pre aren't handled by ST3 and require manual adjustment
    for pre_element in soup.find_all("pre"):
        # select the first child, <code>
        code_element = next(pre_element.children)
        fixed_pre = str(code_element).replace(" ", '<i class="space">.</i>').replace("\n", "<br />")
        code_element.replace_with(bs4.BeautifulSoup(fixed_pre, "html.parser"))

    # FIXME:
    # - report that ST doesn't support <br/> but does work with <br />... WTF?
    return "<style>\n{}\n</style>\n\n{}".format(resources["stylesheet"], soup).replace("<br/>", "<br />")


images_cache = {}
images_loading = []


def get_base64_image(path, re_render, resources):
    """Gets the base64 for the image (local and remote images).

    re_render is a callback which is called when we finish loading an image
    from the internet to trigger an update of the preview (the image will then
    be loaded from the cache)

    return base64_data, (width, height)
    """

    def callback(path, resources, future):
        """Altering images_cache is "safe" to do because callback is called in
        the same thread as add_done_callback:
        > Added callables are called in the order that they were added and are
        > always called in a thread belonging to the process that added them
        (Python docs)
        """
        try:
            images_cache[path] = future.result()
        except urllib.error.HTTPError as e:
            images_cache[path] = resources["base64_404_image"]
            print("Error loading {!r}: {!r}".format(path, e))

        images_loading.remove(path)

        # we render, which means this function will be called again, but this
        # time, we will read from the cache
        re_render()

    if path in images_cache:
        return images_cache[path]

    if path.startswith("http://") or path.startswith("https://"):
        if path not in images_loading:
            executor.submit(load_image, path).add_done_callback(partial(callback, path, resources))
            images_loading.append(path)
        return resources["base64_loading_image"]

    with open(path, "rb") as fhandle:
        image_content = fhandle.read()
        width, height = get_image_size(io.BytesIO(image_content), path)
        image = "data:image/png;base64," + base64.b64encode(image_content).decode("utf-8")
        images_cache[path] = image, (width, height)
        return images_cache[path]


def load_image(url):
    with urllib.request.urlopen(url, timeout=60) as conn:
        image_content = conn.read()
        width, height = get_image_size(io.BytesIO(image_content), url)
        content_type = conn.info().get_content_type()
        if "image" not in content_type:
            raise ValueError("{!r} doesn't point to an image, but to a {!r}".format(url, content_type))
        image_content = base64.b64encode(image_content).decode("utf-8")
        return "data:image/png;base64," + image_content, (width, height)


def get_image_size(fhandle, pathlike):
    """Thanks to https://stackoverflow.com/a/20380514/6164984

    fhandle should be a seekable stream. It's not the best for non-seekable
    streams, but in our case, we have to load the whole stream into memory
    anyway because base64 library only accepts bytes-like objects, and not
    streams.
    pathlike is the filename/path/url of the image so that we can guess the
    file format
    """

    format_ = os.path.splitext(os.path.basename(pathlike))[1][1:]

    head = fhandle.read(24)
    if len(head) != 24:
        return "invalid head"
    if format_ == "png":
        check = struct.unpack(">i", head[4:8])[0]
        if check != 0x0D0A1A0A:
            return
        width, height = struct.unpack(">ii", head[16:24])
    elif format_ == "gif":
        width, height = struct.unpack("<HH", head[6:10])
    elif format_ == "jpeg":
        try:
            fhandle.seek(0)  # Read 0xff next

            size = 2
            ftype = 0
            while not 0xC0 <= ftype <= 0xCF:
                fhandle.seek(size, 1)
                byte = fhandle.read(1)
                if byte == b"":
                    fhandle = end  # noqa
                    byte = fhandle.read(1)

                while ord(byte) == 0xFF:
                    byte = fhandle.read(1)
                ftype = ord(byte)
                size = struct.unpack(">H", fhandle.read(2))[0] - 2
            # We are at a SOFn block
            fhandle.seek(1, 1)  # Skip `precision' byte.
            height, width = struct.unpack(">HH", fhandle.read(4))
        except Exception as e:  # IGNORE:W0703
            raise e
    else:
        return "unknown format {!r}".format(format_)
    return width, height
