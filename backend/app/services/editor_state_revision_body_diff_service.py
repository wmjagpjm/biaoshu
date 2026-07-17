"""
模块：P12E-A/P12E-B 修订章节正文差异服务
用途：
  - P12E-A：只读比较所选历史修订与服务端当前 chapters，返回有界行差异；
  - P12E-B：只读比较同一项目两条历史修订 chapters，返回有界行差异。
对接：api.editor_state_revisions 单修订 body-diff GET、双修订 body-diff GET；
  editor_state_service / editor_state_revision_history_service。
二次开发：
  - 全程只读：禁止 add/delete/flush/commit/rollback/refresh/锁/审计/检查点/修订写/HTTP；
  - 历史侧复用 C1 get_editor_state_revision 三重作用域与快照完整性重验；
  - P12E-A 当前侧 get_editor_state→extract_canonical_snapshot 仅取 chapters；
  - P12E-B 禁止读取当前 editor-state，两侧均只取历史快照 chapters；
  - 完整正文先判等，再生成展示截断；禁止用版本/长度/摘要/Python 对象相等冒充；
  - 章节服务端内部唯一 id 配对，缺可用唯一 id 按序号；重复/脏数据固定失败。
"""

from __future__ import annotations

import difflib
from typing import Any

from sqlalchemy.orm import Session

from app.services import editor_state_service
from app.services.editor_state_revision_history_service import (
    EditorStateRevisionHistoryError,
    get_editor_state_revision,
)

# 固定上限（测试精确断言）
MAX_CHAPTERS = 100
MAX_BODY_CODEPOINTS = 20_000
MAX_TITLE_CODEPOINTS = 240
MAX_HUNKS_PER_CHAPTER = 80
MAX_HUNK_TEXT_CODEPOINTS = 2_000
MAX_TOTAL_DIFF_TEXT = 120_000

CODE_BODY_DIFF_FAILED = "editor_state_revision_body_diff_failed"
MSG_BODY_DIFF_FAILED = "修订正文差异生成失败"

KIND_ADDED = "added"
KIND_REMOVED = "removed"
KIND_CHANGED = "changed"
OP_EQUAL = "equal"
OP_DELETE = "delete"
OP_INSERT = "insert"


class EditorStateRevisionBodyDiffError(Exception):
    """
    用途：正文差异固定错误；路由映射 500 + no-store。
    对接：api.editor_state_revisions body-diff。
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _body_diff_failed() -> EditorStateRevisionBodyDiffError:
    """用途：统一构造脱敏差异失败，禁止附带内部异常细节。"""
    return EditorStateRevisionBodyDiffError(
        500, CODE_BODY_DIFF_FAILED, MSG_BODY_DIFF_FAILED
    )


def _codepoints(text: str) -> list[str]:
    """用途：按 Unicode 码点序列切片，避免代理对截断。"""
    return list(text)


def _truncate_codepoints(text: str, limit: int) -> tuple[str, bool]:
    """用途：按码点截断；返回 (文本, 是否截断)。"""
    if limit < 0:
        raise _body_diff_failed()
    chars = _codepoints(text)
    if len(chars) <= limit:
        return text, False
    return "".join(chars[:limit]), True


def _normalize_newlines(text: str) -> str:
    """用途：换行规范化为 \\n，再参与逐值比较与行差异。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _to_lines(text: str) -> list[str]:
    """用途：规范化后按行切分（保留行尾），空串返回空列表。"""
    normalized = _normalize_newlines(text)
    if normalized == "":
        return []
    return normalized.splitlines(keepends=True)


def _chapter_title(chapter: dict[str, Any]) -> str:
    """用途：抽取标题；缺失/None 视为空串；非字符串固定失败。"""
    if "title" not in chapter or chapter.get("title") is None:
        return ""
    title = chapter.get("title")
    if not isinstance(title, str):
        raise _body_diff_failed()
    return title


def _chapter_body(chapter: dict[str, Any]) -> str:
    """用途：抽取正文；缺失/None 视为空串；非字符串固定失败。"""
    if "body" not in chapter or chapter.get("body") is None:
        return ""
    body = chapter.get("body")
    if not isinstance(body, str):
        raise _body_diff_failed()
    return body


def _chapter_id(chapter: dict[str, Any]) -> str | None:
    """
    用途：抽取可用唯一 id（非空字符串）；否则返回 None。
    二次开发：id 仅服务端配对，禁止进入响应。
    """
    value = chapter.get("id")
    if not isinstance(value, str):
        return None
    if value == "":
        return None
    return value


def _require_chapter_list(raw: Any) -> list[dict[str, Any]]:
    """
    用途：将 chapters 规范为对象列表；None→[]；非 list/非对象固定失败。
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise _body_diff_failed()
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _body_diff_failed()
        out.append(item)
    return out


def _ids_usable_unique(chapters: list[dict[str, Any]]) -> bool | None:
    """
    用途：判断一侧章节 id 是否全部可用且唯一。
    返回：
      - True：全部非空字符串且无重复，可用 id 配对；
      - False：存在缺失/空/非字符串 id，需回退序号；
      - None：存在重复 id，固定失败。
    """
    seen: dict[str, bool] = {}
    all_present = True
    for ch in chapters:
        cid = _chapter_id(ch)
        if cid is None:
            all_present = False
            continue
        if cid in seen:
            return None
        seen[cid] = True
    if not all_present:
        return False
    # 空列表：可用序号（也等价 id）
    return True


def _pair_chapters(
    current: list[dict[str, Any]],
    target: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]]:
    """
    用途：生成配对结果列表 [(kind, current_ch|None, target_ch|None)]。
    kind 仅为 added/removed/changed（此处 changed 含“同章待比正文”）。
    二次开发：重复 id / 脏章已在调用前失败；不猜测跨序配对。
    """
    cur_usable = _ids_usable_unique(current)
    tgt_usable = _ids_usable_unique(target)
    if cur_usable is None or tgt_usable is None:
        raise _body_diff_failed()

    # 两侧均具备可用唯一 id 时按 id 配对；任一侧缺 id 则按序号
    use_id = cur_usable is True and tgt_usable is True

    pairs: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]] = []
    if use_id:
        cur_map: dict[str, dict[str, Any]] = {}
        for ch in current:
            cid = _chapter_id(ch)
            assert cid is not None
            cur_map[cid] = ch
        tgt_map: dict[str, dict[str, Any]] = {}
        for ch in target:
            cid = _chapter_id(ch)
            assert cid is not None
            tgt_map[cid] = ch
        # 当前序：added / 同 id 待比
        for ch in current:
            cid = _chapter_id(ch)
            assert cid is not None
            if cid in tgt_map:
                pairs.append((KIND_CHANGED, ch, tgt_map[cid]))
            else:
                pairs.append((KIND_ADDED, ch, None))
        # 目标独有：removed（按目标序）
        for ch in target:
            cid = _chapter_id(ch)
            assert cid is not None
            if cid not in cur_map:
                pairs.append((KIND_REMOVED, None, ch))
        return pairs

    # 序号配对
    n = max(len(current), len(target))
    for i in range(n):
        cur = current[i] if i < len(current) else None
        tgt = target[i] if i < len(target) else None
        if cur is not None and tgt is not None:
            pairs.append((KIND_CHANGED, cur, tgt))
        elif cur is not None:
            pairs.append((KIND_ADDED, cur, None))
        else:
            pairs.append((KIND_REMOVED, None, tgt))
    return pairs


def _diff_lines(before: str, after: str) -> list[dict[str, str]]:
    """
    用途：对规范化行序列生成 equal/delete/insert hunks。
    二次开发：replace 拆成 delete+insert；autojunk=False 避免启发式吞行。
    """
    a = _to_lines(before)
    b = _to_lines(after)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    hunks: list[dict[str, str]] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            text = "".join(a[i1:i2])
            if text != "":
                hunks.append({"op": OP_EQUAL, "text": text})
        elif tag == "delete":
            text = "".join(a[i1:i2])
            if text != "":
                hunks.append({"op": OP_DELETE, "text": text})
        elif tag == "insert":
            text = "".join(b[j1:j2])
            if text != "":
                hunks.append({"op": OP_INSERT, "text": text})
        elif tag == "replace":
            del_text = "".join(a[i1:i2])
            ins_text = "".join(b[j1:j2])
            if del_text != "":
                hunks.append({"op": OP_DELETE, "text": del_text})
            if ins_text != "":
                hunks.append({"op": OP_INSERT, "text": ins_text})
        else:
            raise _body_diff_failed()
    return hunks


def _apply_display_bounds(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """
    用途：对已判定的差异 items 做展示截断；不回写 sameBody。
    上限：标题 240、单章 hunks 80、单 hunk 2000、总文本 120000、最多 100 章项。
    """
    truncated = False
    out: list[dict[str, Any]] = []
    total_text = 0

    for idx, item in enumerate(items):
        if idx >= MAX_CHAPTERS:
            truncated = True
            break

        before_title, t1 = _truncate_codepoints(
            item["before_title"], MAX_TITLE_CODEPOINTS
        )
        after_title, t2 = _truncate_codepoints(
            item["after_title"], MAX_TITLE_CODEPOINTS
        )
        if t1 or t2:
            truncated = True

        raw_hunks: list[dict[str, str]] = item["hunks"]
        bounded_hunks: list[dict[str, str]] = []
        for h_i, hunk in enumerate(raw_hunks):
            if h_i >= MAX_HUNKS_PER_CHAPTER:
                truncated = True
                break
            text, t_h = _truncate_codepoints(
                hunk["text"], MAX_HUNK_TEXT_CODEPOINTS
            )
            if t_h:
                truncated = True
            # 总文本预算
            text_len = len(_codepoints(text))
            if total_text + text_len > MAX_TOTAL_DIFF_TEXT:
                remain = MAX_TOTAL_DIFF_TEXT - total_text
                if remain <= 0:
                    truncated = True
                    break
                text, _ = _truncate_codepoints(text, remain)
                truncated = True
                bounded_hunks.append({"op": hunk["op"], "text": text})
                total_text = MAX_TOTAL_DIFF_TEXT
                break
            bounded_hunks.append({"op": hunk["op"], "text": text})
            total_text += text_len
            if total_text >= MAX_TOTAL_DIFF_TEXT:
                # 刚好用尽：后续项截断
                if h_i + 1 < len(raw_hunks) or idx + 1 < len(items):
                    truncated = True
                break

        out.append(
            {
                "ordinal": len(out) + 1,
                "kind": item["kind"],
                "before_title": before_title,
                "after_title": after_title,
                "hunks": bounded_hunks,
            }
        )
        if total_text >= MAX_TOTAL_DIFF_TEXT and idx + 1 < min(
            len(items), MAX_CHAPTERS
        ):
            truncated = True
            break

    return out, truncated


def _display_body(body: str) -> tuple[str, bool]:
    """
    用途：仅用于展示/difflib 的正文截断；完整判等在此之前完成。
    二次开发：必须在 SequenceMatcher 前截断，防止超长正文资源风险与假绿。
    """
    return _truncate_codepoints(body, MAX_BODY_CODEPOINTS)


def _build_raw_items(
    pairs: list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]],
) -> tuple[list[dict[str, Any]], bool, bool]:
    """
    用途：完整正文先判等；最多前 MAX_CHAPTERS 个实际正文差异章进 difflib。
    返回：(raw_items, any_body_diff, display_truncated)。
    二次开发：
      - 完整值扫描覆盖全部配对（含展示 cap 之后的章节）；
      - 仅对最多 100 个实际正文差异（changed/added/removed）生成 hunks；
      - 标题变化不单独制造正文差异；
      - 差异仅在码点截断点之后时仍记 any_diff=true，items 保留该章；
      - 第 101 个及以后差异只记存在性/截断，禁止再进 SequenceMatcher。
    """
    items: list[dict[str, Any]] = []
    any_diff = False
    display_trunc = False
    for kind, cur, tgt in pairs:
        if kind == KIND_CHANGED:
            assert cur is not None and tgt is not None
            before_body = _chapter_body(tgt)
            after_body = _chapter_body(cur)
            before_title = _chapter_title(tgt)
            after_title = _chapter_title(cur)
            # 完整规范化正文判等；标题变化不单独制造正文差异
            if _normalize_newlines(before_body) == _normalize_newlines(
                after_body
            ):
                continue
            any_diff = True
            # 展示 cap：仅前 MAX_CHAPTERS 个实际正文差异进 difflib
            if len(items) >= MAX_CHAPTERS:
                display_trunc = True
                continue
            before_disp, t_before = _display_body(before_body)
            after_disp, t_after = _display_body(after_body)
            if t_before or t_after:
                display_trunc = True
            hunks = _diff_lines(before_disp, after_disp)
            items.append(
                {
                    "kind": KIND_CHANGED,
                    "before_title": before_title,
                    "after_title": after_title,
                    "hunks": hunks,
                }
            )
        elif kind == KIND_ADDED:
            assert cur is not None
            any_diff = True
            if len(items) >= MAX_CHAPTERS:
                display_trunc = True
                continue
            after_body = _chapter_body(cur)
            after_title = _chapter_title(cur)
            after_disp, t_after = _display_body(after_body)
            if t_after:
                display_trunc = True
            hunks = _diff_lines("", after_disp)
            items.append(
                {
                    "kind": KIND_ADDED,
                    "before_title": "",
                    "after_title": after_title,
                    "hunks": hunks,
                }
            )
        elif kind == KIND_REMOVED:
            assert tgt is not None
            any_diff = True
            if len(items) >= MAX_CHAPTERS:
                display_trunc = True
                continue
            before_body = _chapter_body(tgt)
            before_title = _chapter_title(tgt)
            before_disp, t_before = _display_body(before_body)
            if t_before:
                display_trunc = True
            hunks = _diff_lines(before_disp, "")
            items.append(
                {
                    "kind": KIND_REMOVED,
                    "before_title": before_title,
                    "after_title": "",
                    "hunks": hunks,
                }
            )
        else:
            raise _body_diff_failed()
    return items, any_diff, display_trunc


def _compare_chapter_snapshots(
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """
    用途：纯双快照章节正文比较；参数为 before/after 两侧已校验 dict。
    对接：P12E-A current 路径与 P12E-B 双修订路径共用。
    二次开发：
      - before 对应差异前（P12E-A 的 target/修订侧）；
      - after 对应差异后（P12E-A 的 current 侧）；
      - 返回 before_chapter_count/after_chapter_count；调用方按路由投影。
    """
    if not isinstance(before_snapshot, dict) or not isinstance(
        after_snapshot, dict
    ):
        raise _body_diff_failed()

    before_chapters = _require_chapter_list(before_snapshot.get("chapters"))
    after_chapters = _require_chapter_list(after_snapshot.get("chapters"))

    # 章节数超 100：完整值仍判等，展示侧截断
    over_chapter_cap = (
        len(before_chapters) > MAX_CHAPTERS or len(after_chapters) > MAX_CHAPTERS
    )

    # 配对：after 作 current 侧、before 作 target 侧（added/removed 语义）
    pairs = _pair_chapters(after_chapters, before_chapters)
    raw_items, any_diff, body_trunc = _build_raw_items(pairs)
    same_body = not any_diff

    if same_body:
        return {
            "same_body": True,
            "changed_chapter_count": 0,
            "before_chapter_count": len(before_chapters),
            "after_chapter_count": len(after_chapters),
            "truncated": False,
            "items": [],
        }

    bounded_items, trunc = _apply_display_bounds(raw_items)
    if over_chapter_cap or body_trunc:
        trunc = True
    # 契约：changedChapterCount 必须等于 items.length
    return {
        "same_body": False,
        "changed_chapter_count": len(bounded_items),
        "before_chapter_count": len(before_chapters),
        "after_chapter_count": len(after_chapters),
        "truncated": trunc,
        "items": bounded_items,
    }


def compare_revision_bodies(
    db: Session,
    workspace_id: str,
    project_id: str,
    before_revision_id: str,
    after_revision_id: str,
) -> dict[str, Any]:
    """
    用途：比较同一项目两条历史修订的 chapters，返回脱敏正文差异。
    对接：GET .../editor-state-revisions/{before}/body-diff/{after}
    二次开发：
      - 两侧均复用 C1 get_editor_state_revision 三重作用域与完整性重验；
      - 禁止读取当前 editor-state；
      - 历史 404/corrupt 原样上抛；其他异常固定 body_diff_failed；
      - 不写库、不加锁、不返回 ID/版本/路径/原文异常。
    """
    # 两侧历史快照：任一 404/corrupt 原样上抛
    try:
        before_rev = get_editor_state_revision(
            db, workspace_id, project_id, before_revision_id
        )
        after_rev = get_editor_state_revision(
            db, workspace_id, project_id, after_revision_id
        )
    except EditorStateRevisionHistoryError:
        raise

    try:
        before_snap = before_rev["snapshot"]
        after_snap = after_rev["snapshot"]
        return _compare_chapter_snapshots(before_snap, after_snap)
    except EditorStateRevisionHistoryError:
        raise
    except EditorStateRevisionBodyDiffError:
        raise
    except Exception:
        raise _body_diff_failed() from None


def compare_revision_body_with_current(
    db: Session,
    workspace_id: str,
    project_id: str,
    revision_id: str,
) -> dict[str, Any]:
    """
    用途：比较目标修订 chapters 与当前权威 chapters，返回脱敏正文差异。
    对接：GET .../editor-state-revisions/{revisionId}/body-diff。
    二次开发：
      - 历史服务 404/corrupt 原样上抛；
      - 当前读取/配对/差异其他异常固定 body_diff_failed；
      - 不写库、不加锁、不返回 ID/版本/路径/原文异常；
      - 响应仍投影 currentChapterCount/targetChapterCount（P12E-A 不变）。
    """
    # 目标侧：三重作用域 + 规范 13 键重验（C1）
    try:
        target = get_editor_state_revision(
            db, workspace_id, project_id, revision_id
        )
    except EditorStateRevisionHistoryError:
        raise

    try:
        current_state = editor_state_service.get_editor_state(
            db, workspace_id, project_id
        )
        current_snap = editor_state_service.extract_canonical_snapshot(
            current_state
        )
        target_snap = target["snapshot"]
        result = _compare_chapter_snapshots(target_snap, current_snap)
        # P12E-A 对外键名：current=after 侧，target=before 侧
        return {
            "same_body": result["same_body"],
            "changed_chapter_count": result["changed_chapter_count"],
            "current_chapter_count": result["after_chapter_count"],
            "target_chapter_count": result["before_chapter_count"],
            "truncated": result["truncated"],
            "items": result["items"],
        }
    except EditorStateRevisionHistoryError:
        raise
    except EditorStateRevisionBodyDiffError:
        raise
    except Exception:
        raise _body_diff_failed() from None
