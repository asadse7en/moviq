from importlib import import_module

_debug_trace = import_module(".debug_trace", __package__)
for _name in dir(_debug_trace):
    globals()[_name] = getattr(_debug_trace, _name)
