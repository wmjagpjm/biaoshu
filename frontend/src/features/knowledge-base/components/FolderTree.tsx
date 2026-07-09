import { Folder, FolderPlus, Layers } from "lucide-react";
import type { KbFolder } from "../types";
import { KB_FOLDER_ALL } from "../types";

/**
 * 模块：知识库文件夹树
 * 用途：左侧选择「全部」或具体文件夹；支持新建文件夹。
 */

export type FolderTreeProps = {
  folders: KbFolder[];
  counts: Map<string, number>;
  totalCount: number;
  selectedId: string;
  onSelect: (id: string) => void;
  onCreate: (name: string) => void;
};

export function FolderTree({
  folders,
  counts,
  totalCount,
  selectedId,
  onSelect,
  onCreate,
}: FolderTreeProps) {
  function handleCreate() {
    const name = window.prompt("新建文件夹名称", "新文件夹");
    if (name?.trim()) onCreate(name.trim());
  }

  return (
    <aside className="kb-folder-tree" aria-label="知识库文件夹">
      <div className="kb-folder-tree__head">
        <strong>文件夹</strong>
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={handleCreate}
          title="新建文件夹"
        >
          <FolderPlus size={14} />
        </button>
      </div>
      <nav className="kb-folder-tree__list">
        <button
          type="button"
          className={`kb-folder-item${selectedId === KB_FOLDER_ALL ? " is-active" : ""}`}
          onClick={() => onSelect(KB_FOLDER_ALL)}
        >
          <Layers size={16} />
          <span className="kb-folder-item__name">全部文档</span>
          <span className="kb-folder-item__count mono">{totalCount}</span>
        </button>
        {folders.map((f) => (
          <button
            key={f.id}
            type="button"
            className={`kb-folder-item${selectedId === f.id ? " is-active" : ""}`}
            onClick={() => onSelect(f.id)}
          >
            <Folder size={16} />
            <span className="kb-folder-item__name">{f.name}</span>
            <span className="kb-folder-item__count mono">
              {counts.get(f.id) ?? 0}
            </span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
