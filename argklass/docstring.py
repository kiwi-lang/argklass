import inspect
import re
from dataclasses import dataclass


@dataclass
class SourceCursor:
    i: int = 0


docstring_oneline = re.compile(r'(\s*)"""(.*)"""')
docstring_start = re.compile(r'(\s*)"""(.*)')
docstring_end = re.compile(r'(.*)"""')
attr_line = re.compile(r"(\s*)(?P<varname>[A-Za-z_]*)(:?)(.*)(=?)(.*)?#(?P<comment>.*)")


class DocstringIterator:
    """Looks for docstring included inherited fields"""

    def __init__(self, dataclass) -> None:
        parents = dataclass.__mro__
        self.classes = parents[:-1]

        self.cursors = []
        self.sources = []
        for cls in self.classes:
            self.cursors.append(SourceCursor())
            self.sources.append(inspect.getsource(cls).splitlines())

    def get_dataclass_docstring(self):
        docstrings = []

        for source, cursor in zip(self.sources, self.cursors):
            recognized = 0
            started = False
            docstring_lines = []
            # Where the class docstring ended, and so where a later
            # find_field() should start looking. 0 when there is none: the
            # fields begin at the top of the body.
            end = 0

            for i, line in enumerate(source):
                if "@dataclass" in line:
                    recognized += 1
                    continue

                if "class " in line:
                    recognized += 1
                    continue

                if recognized < 2:
                    continue

                if not started:
                    if not line.strip():
                        continue

                    if docstring_oneline.match(line):
                        docstring_lines.append(line.strip()[3:-3])
                        end = i
                        break

                    if docstring_start.match(line):
                        started = True
                        docstring_lines.append(line.strip()[3:])
                        continue

                    # The first real statement of the class body. A class
                    # docstring can only be here, so there is not one -- and
                    # scanning on would find a METHOD's docstring instead and
                    # leave the cursor past every field, which is how a
                    # dataclass with documented methods but no class docstring
                    # used to lose the help text for all of its arguments.
                    break

                if docstring_end.match(line):
                    docstring_lines.append(line.strip()[:-3])
                    started = False
                    end = i
                    break

                docstring_lines.append(line.strip())

            cursor.i = end
            if len(docstring_lines) > 0:
                docstrings.append("\n".join(docstring_lines))

        if len(docstrings) > 0:
            return docstrings[0]

        return None

    def find_field(self, field):

        for source, cursor in zip(self.sources, self.cursors):
            start = cursor.i
            nlines = len(source)
            comment = None

            while start < nlines:
                line = source[start]

                if match := attr_line.match(line):
                    values = match.groupdict()

                    if values.get("varname", "") == field.name:
                        comment = values.get("comment")
                        break

                start += 1

            # No found
            if start >= nlines and comment is None:
                continue

            docstring = comment.strip()
            cursor.i = start
            return docstring

        return None
