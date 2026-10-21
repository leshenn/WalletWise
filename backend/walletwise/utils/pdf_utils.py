from .text_utils import word_center_x, word_center_y


def group_words_into_lines(words, y_tolerance: float):
    if not words:
        return []

    words = sorted(words, key=lambda word: (word_center_y(word), word[0]))
    lines = []
    current = []
    current_y = None

    for word in words:
        y = word_center_y(word)

        if current_y is None:
            current = [word]
            current_y = y
            continue

        if abs(y - current_y) <= y_tolerance:
            current.append(word)
            current_y = sum(word_center_y(item) for item in current) / len(current)
        else:
            lines.append(sorted(current, key=lambda item: item[0]))
            current = [word]
            current_y = y

    if current:
        lines.append(sorted(current, key=lambda item: item[0]))

    return lines
