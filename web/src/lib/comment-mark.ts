/**
 * TipTap Comment Mark — 自研薄 comment 标记扩展。
 *
 * 渲染为 <span data-thread-id="..." class="comment-highlight">，
 * 点击事件由 AnnotationLayer 通过 ProseMirror plugin 捕获。
 */

import { Mark, mergeAttributes } from "@tiptap/core"

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    comment: {
      setComment: (threadId: string) => ReturnType
      unsetComment: (threadId: string) => ReturnType
    }
  }
}

export const CommentMark = Mark.create({
  name: "comment",

  addAttributes() {
    return {
      threadId: {
        default: null,
        parseHTML: (element) => element.getAttribute("data-thread-id"),
        renderHTML: (attributes) => ({
          "data-thread-id": attributes.threadId,
        }),
      },
    }
  },

  parseHTML() {
    return [{ tag: "span[data-thread-id]" }]
  },

  renderHTML({ HTMLAttributes }) {
    return [
      "span",
      mergeAttributes(HTMLAttributes, { class: "comment-highlight" }),
      0,
    ]
  },

  addCommands() {
    return {
      setComment:
        (threadId: string) =>
        ({ commands }) => {
          return commands.setMark(this.name, { threadId })
        },
      unsetComment:
        (threadId: string) =>
        ({ state, dispatch }) => {
          const { from, to } = state.selection
          if (!dispatch) return true
          state.doc.nodesBetween(from, to, (node, pos) => {
            if (node.marks.some((m) => m.type.name === this.name && m.attrs.threadId === threadId)) {
              const markType = state.schema.marks[this.name]
              dispatch(
                state.tr.removeMark(
                  pos,
                  pos + node.nodeSize,
                  markType.create({ threadId }),
                ),
              )
            }
          })
          return true
        },
    }
  },
})

export default CommentMark
