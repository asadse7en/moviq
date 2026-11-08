

class GeneralTabMixin:
    """Backward-compatible theme indicator helper for older settings tests."""

    @staticmethod
    def _update_theme_options(view, palette):
        theme_var = view.vars.get("theme")
        selected_theme = theme_var.get() if theme_var is not None else None

        for name, (indicator, ring_name, dot_name) in getattr(
            view, "_theme_option_indicators", {}
        ).items():
            indicator.configure(background=palette["bg"])
            indicator.itemconfigure(
                ring_name,
                outline=(
                    palette["accent"] if name == selected_theme
                    else palette["header_border_hi"]
                ),
            )
            indicator.itemconfigure(
                dot_name,
                fill=palette["accent"],
                state="normal" if name == selected_theme else "hidden",
            )
