"""在执行浏览器测试前检查模型不能破坏的入口契约。"""
from collections import Counter
from html.parser import HTMLParser


class GameHtml(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids = Counter()
        self.start = None
        self.start_text = []
        self.in_start = False

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        name = values.get('id')
        if name:
            self.ids[name] += 1
        if name == 'start':
            self.start = (tag, values)
            self.in_start = True

    def handle_data(self, data):
        if self.in_start:
            self.start_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'button':
            self.in_start = False


def validate_controls(candidate):
    parsed = GameHtml()
    parsed.feed((candidate/'game/index.html').read_text())
    duplicates = sorted(name for name, count in parsed.ids.items() if count > 1)
    if duplicates:
        raise ValueError('Control contract: duplicate HTML IDs: '+', '.join(duplicates))
    for name in ('start', 'pause', 'arena', 'overlay'):
        if parsed.ids[name] != 1:
            raise ValueError('Control contract: preserve the existing #'+name+' element')
    tag, attrs = parsed.start
    if tag != 'button' or ''.join(parsed.start_text).strip() != '开始游戏 →':
        raise ValueError('Control contract: #start must remain the 开始游戏 → button before launch')
    if any(key in attrs for key in ('aria-label', 'aria-labelledby', 'hidden', 'disabled')):
        raise ValueError('Control contract: #start is the SAME button for 开始游戏 → and 再次出航 →. Do not add static aria-label/aria-labelledby, hidden or disabled in HTML; its visible text supplies its accessible name. Use title or aria-describedby for extra help.')
    if parsed.ids['restart']:
        raise ValueError('Control contract: do not invent #restart; main.js reuses #start for restart')
