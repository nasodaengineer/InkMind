/* TipTap 编辑器组件 — 生成中只读，forwardRef 暴露 appendContent/setContent/clearContent */

import { forwardRef, useImperativeHandle, useEffect } from "react"
import { useEditor, EditorContent } from "@tiptap/react"
import StarterKit from "@tiptap/starter-kit"

export interface EditorHandle {
  appendContent: (text: string) => void
  setContent: (text: string) => void
  clearContent: () => void
}

export interface EditorProps {
  content?: string
  readonly?: boolean
  onUpdate?: (html: string) => void
  placeholder?: string
}

const Editor = forwardRef<EditorHandle, EditorProps>(function Editor(
  { content = "", readonly = false, onUpdate, placeholder },
  ref,
) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: {
          levels: [1, 2, 3],
        },
      }),
    ],
    content,
    onUpdate: ({ editor }) => {
      onUpdate?.(editor.getHTML())
    },
    editorProps: {
      attributes: {
        class:
          "prose prose-sm prose-stone dark:prose-invert max-w-none focus:outline-none min-h-[300px] px-4 py-3",
      },
    },
  })

  // 同步 readonly 状态
  useEffect(() => {
    if (editor) {
      editor.setEditable(!readonly)
    }
  }, [editor, readonly])

  useImperativeHandle(ref, () => ({
    appendContent(text: string) {
      if (!editor) return
      // 在文档末尾追加内容。使用 insertContentAt 确保始终追加在最后。
      const end = editor.state.doc.content.size
      editor.commands.insertContentAt(
        { from: end, to: end },
        text,
        { updateSelection: false },
      )
    },
    setContent(text: string) {
      if (!editor) return
      editor.commands.setContent(text)
    },
    clearContent() {
      if (!editor) return
      editor.commands.clearContent()
    },
  }))

  if (!editor) {
    return (
      <div className="border rounded-md bg-card">
        <div className="prose prose-sm max-w-none px-4 py-3 text-muted-foreground">
          {placeholder || "选择章节开始编辑..."}
        </div>
      </div>
    )
  }

  return (
    <div className="border rounded-md bg-card">
      <EditorContent editor={editor} />
    </div>
  )
})

export default Editor
