import io

from PIL import Image, ImageTk
import requests

from ... import __version__ as APP_VERSION
from ._common import DETAIL_POSTER_SIZE, IMAGE_BASE


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
            response = requests.get(
                poster_path if str(poster_path).startswith("https://")
                else f"{IMAGE_BASE}/w185{poster_path}",
                headers={"User-Agent": f"Moviq/{APP_VERSION}"},
                timeout=12,
            )
            response.raise_for_status()
            image_data = response.content
            error = None
        except Exception as exception:
            image_data = None
            error = str(exception)
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
