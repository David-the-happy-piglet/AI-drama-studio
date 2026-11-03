import type { Character, CharacterBinding } from "./types";

export const activeElementStatuses = ["submitting", "submitted", "processing", "unknown"];
export const elementStatusNames: Record<string, string> = {
  submitting: "正在提交", submitted: "等待处理", processing: "创建中",
  unknown: "结果待核实", succeeded: "已绑定", failed: "创建失败",
};

export function bindingLabel(character: Character, binding?: CharacterBinding): string {
  if (!binding) return "未创建可灵主体";
  if (binding.source_version !== character.version) return "本地已更新 · 需重新绑定";
  return elementStatusNames[binding.status] || binding.status;
}
