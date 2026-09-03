import { Trash2Icon } from "lucide-react"
import React from "react"

import type { DrawingRecord, DrawingStyle } from "@/features/chart/plugins/types"
import { cn } from "@/lib/utils"

interface DrawingPropertiesBarProps {
  selectedRecord: DrawingRecord | null
  onUpdateStyle: (style: Partial<DrawingStyle>) => void
  onDelete: () => void
}

const colorPresets = [
  { name: "Blue", value: "#38bdf8" },
  { name: "Green", value: "#22c55e" },
  { name: "Red", value: "#ef4444" },
  { name: "Amber", value: "#f59e0b" },
  { name: "Purple", value: "#8b5cf6" },
  { name: "White", value: "#ffffff" },
]

export const DrawingPropertiesBar: React.FC<DrawingPropertiesBarProps> = ({
  selectedRecord,
  onUpdateStyle,
  onDelete,
}) => {
  if (!selectedRecord) return null

  const currentColor = selectedRecord.style?.color ?? "#38bdf8"
  const currentLineWidth = selectedRecord.style?.lineWidth ?? 2

  return (
    <div className="absolute top-12 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-lg border border-[#263246] bg-[#101826] px-3 py-1.5 shadow-xl font-mono text-xs text-[#cbd5e1] backdrop-blur-md">
      <span className="text-[11px] font-semibold text-[#8492a6] uppercase tracking-wider">
        {selectedRecord.type}
      </span>

      <div className="h-4 w-px bg-[#263246]" />

      {/* Color Palette */}
      <div className="flex items-center gap-1.5">
        {colorPresets.map((c) => (
          <button
            key={c.value}
            aria-label={c.name}
            className={cn(
              "h-4 w-4 rounded-full border border-black/40 transition-transform hover:scale-110",
              currentColor === c.value && "ring-2 ring-white ring-offset-1 ring-offset-[#101826]",
            )}
            style={{ backgroundColor: c.value }}
            onClick={() => onUpdateStyle({ color: c.value })}
            title={c.name}
            type="button"
          />
        ))}
      </div>

      <div className="h-4 w-px bg-[#263246]" />

      {/* Line Width */}
      <div className="flex items-center gap-1">
        {[1, 2, 3].map((w) => (
          <button
            key={w}
            className={cn(
              "flex h-5 w-5 items-center justify-center rounded border text-[10px] font-bold transition-colors",
              currentLineWidth === w
                ? "border-[#38bdf8] bg-[#38bdf8]/20 text-[#38bdf8]"
                : "border-transparent text-[#8492a6] hover:bg-[#263246] hover:text-[#cbd5e1]",
            )}
            onClick={() => onUpdateStyle({ lineWidth: w })}
            title={`Line Width ${w}px`}
            type="button"
          >
            {w}px
          </button>
        ))}
      </div>

      <div className="h-4 w-px bg-[#263246]" />

      {/* Delete Button */}
      <button
        aria-label="Delete drawing"
        className="inline-flex h-6 w-6 items-center justify-center rounded text-[#8492a6] transition-colors hover:bg-[#ef4444]/20 hover:text-[#ef4444]"
        onClick={onDelete}
        title="Delete drawing (Delete / Backspace)"
        type="button"
      >
        <Trash2Icon className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}
