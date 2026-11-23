#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

import fitz  # PyMuPDF, used only when importing the PDF sources.


TAG_RE = re.compile(r"(?m)^\[([A-Z])\]")
FIELD_RE = re.compile(r"^\[([A-Z])\]")


def normalize(text):
    # Keep word separators in Latin text without inserting spaces inside Chinese words.
    text = re.sub(r"(?<=[a-zA-Z])\n+(?=[a-zA-Z])", " ", text)
    return re.sub(r"[ \t]+", " ", text.replace("\n", "").strip())


def read_pdf(path):
    with fitz.open(path) as pdf:
        return "\n".join(
            block[4] for page in pdf for block in page.get_text("blocks")
            if not (block[1] > page.rect.height * .9 and block[4].strip() == str(page.number + 1))
        )


def parse_blocks(text):
    positions = [match.start() for match in re.finditer(r"(?m)^\[J\]", text)]
    blocks = []
    for index, start in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(text)
        blocks.append(text[start:end].strip())
    return blocks


def parse_block(block):
    matches = list(TAG_RE.finditer(block))
    fields = {}
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(block)
        segment = block[start:end].strip()
        header = FIELD_RE.match(segment)
        if not header:
            continue
        key = header.group(1)
        value = segment[3:].strip()
        fields[key] = normalize(value)

    options = {}
    for key in ("A", "B", "C", "D"):
        if key in fields:
            options[key] = fields[key]

    answer = list(fields.get("T", ""))
    return {
        "sourceId": fields.get("J", ""),
        "category": fields.get("P", ""),
        "type": fields.get("I", ""),
        "question": fields.get("Q", ""),
        "answer": answer,
        "options": options,
        "multi": len(answer) > 1,
        **({"figure": fields["F"]} if "F" in fields else {}),
    }


def validate(questions):
    problems = []
    seen = set()
    for index, question in enumerate(questions, 1):
        prefix = f"#{index} {question.get('type') or question.get('sourceId')}"
        if not question["sourceId"]:
            problems.append(f"{prefix}: missing sourceId")
        if not question["category"]:
            problems.append(f"{prefix}: missing category")
        if not question["type"]:
            problems.append(f"{prefix}: missing type")
        if not question["question"]:
            problems.append(f"{prefix}: missing question")
        if not question["answer"]:
            problems.append(f"{prefix}: missing answer")
        for answer in question["answer"]:
            if answer not in question["options"]:
                problems.append(f"{prefix}: answer {answer} has no matching option")
        if len(question["options"]) < 4:
            problems.append(f"{prefix}: expected A-D options")
        unique_key = question["type"]
        if unique_key in seen:
            problems.append(f"{prefix}: duplicate type id")
        seen.add(unique_key)
    return problems


def read_bank(path):
    questions = [parse_block(block) for block in parse_blocks(read_pdf(path))]
    problems = validate(questions)
    if not questions or problems:
        raise ValueError(f"{path.name}: invalid question bank {problems[:5]}")
    return {
        "title": path.stem,
        "source": path.name,
        "count": len(questions),
        "categories": sorted({q["category"] for q in questions}),
        "questions": questions,
    }


def write_bank(payload, path, legacy_js=False):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")
    if legacy_js:
        path.with_suffix(".js").write_text(f"window.QUESTION_BANK = {text};\n", encoding="utf-8")


def extract_figures(path, expected):
    figures = {}
    with fitz.open(path) as pdf:
        for page in pdf:
            images = page.get_image_info(xrefs=True)
            for left, top, right, _, label, *_ in page.get_text("words"):
                # Printed as LK060 between LK0602 and LK0604: the high-pass circuit in LK0603.
                name = ("LK0603" if label == "LK060" else label) + ".jpg"
                center = (left + right) / 2
                matches = [im for im in images if im["bbox"][0] <= center <= im["bbox"][2]
                           and 0 <= top - im["bbox"][3] < 18]
                if len(matches) != 1 or name in figures:
                    raise ValueError(f"Ambiguous figure {name} on page {page.number + 1}")
                image = pdf.extract_image(matches[0]["xref"])
                if image["ext"] != "jpeg":
                    raise ValueError(f"Unexpected image format for {name}")
                figures[name] = image["image"]
    if figures.keys() != expected:
        raise ValueError(f"Figure references differ: {figures.keys() ^ expected}")
    return figures


def import_banks(source, output):
    banks = {}
    previous = set()
    expected = set()
    for level in "ABC":
        bank = read_bank(source / f"{level}类题库.pdf")
        ids = [q["type"] for q in bank["questions"]]
        bank["upgradeIds"] = [i for i in ids if i not in previous] if previous else []
        previous.update(ids)
        expected.update(q["figure"] for q in bank["questions"] if "figure" in q)
        banks[level] = bank
    figures = extract_figures(source / "总题库附图标记.pdf", expected)
    (output / "figures").mkdir(parents=True, exist_ok=True)
    for name, data in figures.items():
        (output / "figures" / name).write_bytes(data)
    for level, bank in banks.items():
        name = "questions.json" if level == "A" else f"questions-{level}.json"
        write_bank(bank, output / name, legacy_js=level == "A")
        print(f"{level}: {bank['count']} questions, {len(bank['upgradeIds'])} upgrade questions")


def main():
    parser = argparse.ArgumentParser(description="Import a bank PDF or the complete A/B/C PDF directory.")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.source.is_dir():
        import_banks(args.source, args.output)
    else:
        write_bank(read_bank(args.source), args.output, legacy_js=args.output.suffix == ".json")


if __name__ == "__main__":
    main()
