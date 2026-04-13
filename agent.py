"""
Project Manager Agent — AutoProject 2026-04-08
Quản lý toàn bộ MyProject: Python tools, MQL5 bots, Skills, Documentation.
"""
import anyio
import sys
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AgentDefinition,
    ResultMessage,
    AssistantMessage,
    TextBlock,
)

PROJECT_ROOT = "d:/MyProject"

SYSTEM_PROMPT = """Bạn là Project Manager Agent của MyProject — một hệ thống gồm:
- Trading bots MQL5 (GridHedgeBot, Gold bots)
- Python tools (Streamlit apps, backtesting, ML models)
- Skill-based agent system (skill packages, workflows)
- Web apps (React/Vite frontend)

Nguyên tắc làm việc:
1. Đọc code trước khi sửa — không đoán mò
2. Báo cáo rõ từng bước đang làm
3. Nếu không chắc → hỏi lại, không tự ý làm liều
4. Ưu tiên giữ nguyên logic gốc, chỉ sửa đúng yêu cầu

Khi phân tích project: dùng Glob và Grep để khám phá, đừng assume cấu trúc."""

AGENTS = {
    "code-reviewer": AgentDefinition(
        description="Review code Python và MQL5: tìm bugs, logic errors, performance issues",
        prompt="""Bạn là code reviewer chuyên Python và MQL5.
- Đọc code cẩn thận, báo số dòng cụ thể
- Phân loại: Critical / Warning / Suggestion
- Với MQL5: chú ý memory management, indicator buffers, OnTick logic
- Với Python: chú ý pandas operations, streamlit state, yfinance API calls""",
        tools=["Read", "Glob", "Grep"],
    ),
    "doc-writer": AgentDefinition(
        description="Viết và cập nhật documentation: README, comments, docstrings",
        prompt="""Bạn là technical writer.
- Viết tiếng Việt nếu project dùng tiếng Việt, tiếng Anh nếu ngược lại
- README phải có: mô tả, cài đặt, cách dùng, ví dụ
- Docstrings theo chuẩn Google style
- Không thêm thông tin sai hoặc không có trong code""",
        tools=["Read", "Write", "Edit", "Glob", "Grep"],
    ),
    "test-writer": AgentDefinition(
        description="Viết unit tests cho Python code",
        prompt="""Bạn là QA engineer.
- Dùng pytest
- Test các edge cases quan trọng
- Mock external calls (yfinance, API)
- Đặt file test trong thư mục tests/ hoặc cạnh file gốc""",
        tools=["Read", "Write", "Bash", "Glob"],
    ),
    "file-organizer": AgentDefinition(
        description="Rà soát và tổ chức lại cấu trúc thư mục project",
        prompt="""Bạn là DevOps/project organizer.
- Tìm file trùng lặp, file không dùng
- Đề xuất cấu trúc thư mục hợp lý
- KHÔNG xóa file — chỉ đề xuất, hỏi trước khi di chuyển""",
        tools=["Read", "Glob", "Grep"],
    ),
}


def print_separator(title: str = ""):
    line = "─" * 50
    if title:
        print(f"\n{line}")
        print(f"  {title}")
        print(f"{line}")
    else:
        print(line)


async def run_agent(task: str, working_dir: str = PROJECT_ROOT):
    """Chạy agent với task cho trước."""
    print_separator(f"Task: {task[:60]}{'...' if len(task) > 60 else ''}")
    print(f"  Working dir: {working_dir}\n")

    async for message in query(
        prompt=task,
        options=ClaudeAgentOptions(
            cwd=working_dir,
            system_prompt=SYSTEM_PROMPT,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"],
            permission_mode="acceptEdits",
            max_turns=50,
            agents=AGENTS,
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(block.text)

        elif isinstance(message, ResultMessage):
            print_separator("Hoàn thành")
            if message.stop_reason != "end_turn":
                print(f"Stop reason: {message.stop_reason}")
            return message.result

    return None


async def interactive_mode():
    """Chế độ tương tác — nhập task từ terminal."""
    print("\n" + "=" * 50)
    print("  PROJECT MANAGER AGENT")
    print("  MyProject — AutoProject 2026")
    print("=" * 50)
    print("\nGợi ý task:")
    print("  1. Phân tích toàn bộ cấu trúc MyProject")
    print("  2. Review code AutoProject (Python + MQL5)")
    print("  3. Tạo README cho AutoProject")
    print("  4. Tìm tất cả TODO/FIXME trong code")
    print("  5. So sánh AutoProject V1, V2, V3")
    print("\nGõ 'exit' để thoát.\n")

    while True:
        try:
            task = input("Task > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nThoát.")
            break

        if not task:
            continue
        if task.lower() in ("exit", "quit", "q"):
            print("Thoát.")
            break

        await run_agent(task)
        print()


async def main():
    if len(sys.argv) > 1:
        # Chạy task từ command line: python agent.py "task description"
        task = " ".join(sys.argv[1:])
        await run_agent(task)
    else:
        # Chế độ tương tác
        await interactive_mode()


if __name__ == "__main__":
    anyio.run(main)
