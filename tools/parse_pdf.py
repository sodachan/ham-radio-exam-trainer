#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path

from pypdf import PdfReader


TAG_RE = re.compile(r"(?m)^\[([A-Z])\]")
FIELD_RE = re.compile(r"^\[([A-Z])\]")


def normalize(text):
    return re.sub(r"[ \t]+", " ", text.strip())


def read_pdf(path):
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


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


def main():
    parser = argparse.ArgumentParser(description="Parse amateur radio exam PDF into JSON.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    questions = [parse_block(block) for block in parse_blocks(read_pdf(args.pdf))]
    problems = validate(questions)
    if problems:
        for problem in problems[:20]:
            print(problem)
        raise SystemExit(f"Validation failed with {len(problems)} problem(s).")

    payload = {
        "title": "业余无线电题库",
        "source": str(args.pdf),
        "count": len(questions),
        "categories": sorted({question["category"] for question in questions}),
        "questions": questions,
    }
    json_text = json.dumps(payload, ensure_ascii=False, indent=2)
    args.output.write_text(json_text, encoding="utf-8")
    if args.output.suffix == ".json":
        js_output = args.output.with_suffix(".js")
        js_output.write_text(f"window.QUESTION_BANK = {json_text};\n", encoding="utf-8")
    print(f"Wrote {len(questions)} questions to {args.output}")


if __name__ == "__main__":
    main()
