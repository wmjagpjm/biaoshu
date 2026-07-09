/**
 * 模块：大纲树纯函数
 * 用途：增删改移节点，不依赖 React；便于二开与单测。
 * 对接：后续若后端存扁平路径，可在此做 import/export 适配。
 */

import type { OutlineNode } from "../types";

export function cloneOutline(nodes: OutlineNode[]): OutlineNode[] {
  return nodes.map((n) => ({
    ...n,
    children: n.children ? cloneOutline(n.children) : undefined,
  }));
}

/** 深度优先扁平化（保留树序） */
export function flattenOutline(
  nodes: OutlineNode[],
  acc: OutlineNode[] = [],
): OutlineNode[] {
  for (const n of nodes) {
    acc.push(n);
    if (n.children?.length) flattenOutline(n.children, acc);
  }
  return acc;
}

export function countTargetWords(nodes: OutlineNode[]): number {
  let sum = 0;
  for (const n of flattenOutline(nodes)) {
    if (n.targetWords) sum += n.targetWords;
  }
  return sum;
}

export function findNode(
  nodes: OutlineNode[],
  id: string,
): OutlineNode | null {
  for (const n of nodes) {
    if (n.id === id) return n;
    if (n.children) {
      const found = findNode(n.children, id);
      if (found) return found;
    }
  }
  return null;
}

type ParentLoc = {
  parent: OutlineNode | null;
  siblings: OutlineNode[];
  index: number;
};

function locate(
  nodes: OutlineNode[],
  id: string,
  parent: OutlineNode | null = null,
): ParentLoc | null {
  for (let i = 0; i < nodes.length; i++) {
    if (nodes[i].id === id) {
      return { parent, siblings: nodes, index: i };
    }
    const children = nodes[i].children;
    if (children?.length) {
      const found = locate(children, id, nodes[i]);
      if (found) return found;
    }
  }
  return null;
}

function newId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
}

/** 更新节点字段（不可变） */
export function updateNode(
  nodes: OutlineNode[],
  id: string,
  patch: Partial<Pick<OutlineNode, "title" | "targetWords" | "description">>,
): OutlineNode[] {
  return nodes.map((n) => {
    if (n.id === id) {
      return {
        ...n,
        ...patch,
        targetWords:
          patch.targetWords === undefined
            ? n.targetWords
            : Number.isFinite(patch.targetWords)
              ? patch.targetWords
              : n.targetWords,
      };
    }
    if (n.children?.length) {
      return { ...n, children: updateNode(n.children, id, patch) };
    }
    return n;
  });
}

/** 删除节点（含子孙） */
export function removeNode(nodes: OutlineNode[], id: string): OutlineNode[] {
  return nodes
    .filter((n) => n.id !== id)
    .map((n) =>
      n.children?.length
        ? { ...n, children: removeNode(n.children, id) }
        : n,
    );
}

/**
 * 在参考节点后添加同级；若无参考则追加到根
 * level 与参考节点一致（根级为 1）
 */
export function addSibling(
  nodes: OutlineNode[],
  afterId: string | null,
  title = "新建章节",
): OutlineNode[] {
  const tree = cloneOutline(nodes);

  if (!afterId) {
    tree.push({
      id: newId("ol"),
      title,
      level: 1,
      targetWords: 1500,
      children: [],
    });
    return tree;
  }

  const loc = locate(tree, afterId);
  if (!loc) return tree;

  const ref = loc.siblings[loc.index];
  const level = ref.level;
  const node: OutlineNode = {
    id: newId("ol"),
    title,
    level,
    targetWords: level === 1 ? undefined : 1200,
    children: level < 3 ? [] : undefined,
  };
  loc.siblings.splice(loc.index + 1, 0, node);
  return tree;
}

/**
 * 在父节点下追加子节点（level = parent.level + 1，上限 3）
 */
export function addChild(
  nodes: OutlineNode[],
  parentId: string,
  title = "新建小节",
): OutlineNode[] {
  const tree = cloneOutline(nodes);
  const parent = findNode(tree, parentId);
  if (!parent || parent.level >= 3) return tree;

  const level = (parent.level + 1) as 1 | 2 | 3;
  if (!parent.children) parent.children = [];
  parent.children.push({
    id: newId("ol"),
    title,
    level,
    targetWords: 1000,
    children: level < 3 ? [] : undefined,
  });
  return tree;
}

/** 同级上移 / 下移 */
export function moveNodeAmongSiblings(
  nodes: OutlineNode[],
  id: string,
  direction: "up" | "down",
): OutlineNode[] {
  const tree = cloneOutline(nodes);
  const loc = locate(tree, id);
  if (!loc) return tree;

  const { siblings, index } = loc;
  const target = direction === "up" ? index - 1 : index + 1;
  if (target < 0 || target >= siblings.length) return tree;

  const tmp = siblings[index];
  siblings[index] = siblings[target];
  siblings[target] = tmp;
  return tree;
}

/** 能否上移/下移（UI 禁用态） */
export function canMove(
  nodes: OutlineNode[],
  id: string,
): { up: boolean; down: boolean } {
  const loc = locate(nodes, id);
  if (!loc) return { up: false, down: false };
  return {
    up: loc.index > 0,
    down: loc.index < loc.siblings.length - 1,
  };
}
