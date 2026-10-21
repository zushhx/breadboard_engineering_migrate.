"""Block implicit device writes before PlatformIO can discover/open a serial device."""
from SCons.Script import COMMAND_LINE_TARGETS

blocked = [str(target) for target in COMMAND_LINE_TARGETS
           if any(word in str(target).lower() for word in ['upload', 'erase', 'program'])]
if blocked:
    raise RuntimeError('Implicit device write blocked: ' + ', '.join(blocked) +
                       '. Use the reviewed hardware workflow and explicit user approval.')
