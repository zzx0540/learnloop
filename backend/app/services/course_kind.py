KIND_HINT_V1: dict[str, str] = {
    "knowledge": "",
    "language": (
        "# 课程类型策略 v1\n"
        "这是语言学习课程：出题偏向用法辨析、例句填空和翻译判断；陪伴师回答应附例句。"
    ),
    "programming": (
        "# 课程类型策略 v1\n"
        "这是编程学习课程：出题偏向代码阅读理解和概念辨析；陪伴师回答优先给最小代码示例。"
    ),
}


def append_kind_hint(system_prompt: str, kind: str) -> str:
    hint = KIND_HINT_V1.get(kind, "")
    return system_prompt if not hint else f"{system_prompt}\n\n{hint}"
