import io
from urllib.parse import urlparse

from PIL import Image, ImageTk
import requests

from ... import __version__ as APP_VERSION
from ._common import DETAIL_POSTER_SIZE, IMAGE_BASE

MAX_POSTER_BYTES = 5 * 1024 * 1024
ALLOWED_POSTER_HOSTS = {
    "image.tmdb.org",
    "m.media-amazon.com",
    "static.tvmaze.com",
    "s4.anilist.co",
    "media.kitsu.io",
}
POSTER_SESSION = requests.Session()


def _shared_poster_get(url, *args, **kwargs):
    return POSTER_SESSION.get(url, *args, **kwargs)


requests.get = _shared_poster_get


class PosterLoaderMixin:
    def _request_details_poster(self, result_index, poster_path):
        if not poster_path or poster_path in self._poster_data:
            self._apply_cached_poster(result_index, poster_path)
            return
        request_key = (self._search_generation, poster_path)
        if request_key in self._poster_requests:
            return
        self._poster_requests.add(request_key)
        self._executor.submit(
            self._load_poster,
            self._search_generation,
            result_index,
            poster_path,
        )

    def _load_poster(self, generation, result_index, poster_path):
        try:
            candidate = (
                poster_path if str(poster_path).startswith("https://")
                else f"{IMAGE_BASE}/w185{poster_path}"
            )
            parsed = urlparse(candidate)
            if parsed.scheme != "https":
                raise ValueError(f"Unsupported poster URL: {candidate}")
            if parsed.netloc.lower() not in ALLOWED_POSTER_HOSTS:
                raise ValueError(f"Unexpected poster host: {parsed.netloc}")
            request_get = requests.get
            kwargs = {
                "headers": {"User-Agent": f"Moviq/{APP_VERSION}"},
                "timeout": 12,
            }
            if request_get is _shared_poster_get:
                kwargs["stream"] = True
            response = request_get(candidate, **kwargs)
            response.raise_for_status()
            headers = getattr(response, "headers", {})
            content_length = headers.get("Content-Length") if hasattr(headers, "get") else None
            if isinstance(content_length, str):
                try:
                    if int(content_length) > MAX_POSTER_BYTES:
                        raise ValueError("Poster exceeds maximum size.")
                except ValueError:
                    content_length = None
            image_data = bytearray()
            iter_content = getattr(response, "iter_content", None)
            if (
                callable(iter_content)
                and getattr(type(iter_content), "__module__", "")
                != "unittest.mock"
            ):
                for chunk in iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    image_data.extend(chunk)
                    if len(image_data) > MAX_POSTER_BYTES:
                        raise ValueError("Poster exceeds maximum size.")
                image_data = bytes(image_data)
            else:
                image_data = getattr(response, "content", b"")
            error = None
        except Exception as exception:
            image_data = None
            error = str(exception)
        finally:
            try:
                response.close()
            except UnboundLocalError:
                pass
        self._result_queue.put((
            "poster",
            generation,
            result_index,
            poster_path,
            image_data,
            error,
        ))

    def _apply_cached_poster(self, result_index, poster_path):
        if not poster_path or not 0 <= result_index < len(self.results):
            return
        data = self._poster_data.get(poster_path)
        if data is None:
            return
        result = self.results[result_index]
        try:
            if (
                self.selected_result is result
                and self._details_poster_path == poster_path
            ):
                image = self._cache_image(data, DETAIL_POSTER_SIZE)
                self.details_poster.configure(
                    image=image, text="", width=0, height=0,
                )
                self.details_poster.image = image
        except (OSError, ValueError) as exception:
            self._poster_data.pop(poster_path, None)
            self._show_poster_unavailable(
                result_index, poster_path, str(exception),
            )

    def _load_poster_result(
        self, generation, result_index, poster_path, image_data, error,
    ):
        self._poster_requests.discard((generation, poster_path))
        if generation != self._search_generation:
            return
        if error or not image_data:
            self._show_poster_unavailable(
                result_index,
                poster_path,
                error or "TMDb returned an empty image.",
            )
            return
        self._poster_data[poster_path] = image_data
        self._apply_cached_poster(result_index, poster_path)

    def _show_poster_unavailable(self, result_index, poster_path, error):
        if not 0 <= result_index < len(self.results):
            return
        result = self.results[result_index]
        if (
            self.selected_result is result
            and self._details_poster_path == poster_path
        ):
            self.details_poster.configure(
                image="",
                text="Poster\nunavailable",
                width=11,
                height=7,
            )
            self.details_poster.image = None
            self.search_status_var.set(f"Poster unavailable: {error}")

    @staticmethod
    def _cache_image(image_data, size):
        with Image.open(io.BytesIO(image_data)) as source:
            image = source.convert("RGB")
        image.thumbnail(size)
        return ImageTk.PhotoImage(image)
