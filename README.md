# AIGC 创作引擎

用于短剧、漫剧和剧情短片的 Codex 技能。阶段入口和执行规则见 [SKILL.md](SKILL.md)。

## 安装

把完整技能目录放到以下任一位置，保留 `SKILL.md`、阶段说明及模板的相对路径。

| 使用范围 | 技能目录 |
| --- | --- |
| 个人 | `~/.agents/skills/aigc-creation-engine/` |
| 单个项目 | `<项目目录>/.agents/skills/aigc-creation-engine/` |

首次安装可以用 GitHub CLI 获取仓库。

```sh
mkdir -p "$HOME/.agents/skills"
gh repo clone Sun-dong-dong/aigc-creation-engine "$HOME/.agents/skills/aigc-creation-engine"
```

## 调用

在 Codex 中输入 `$aigc-creation-engine`，提供当前材料或路径、任务范围和交付要求。涉及写入或实际生成时，说明授权范围。

```text
$aigc-creation-engine
按当前剧本和分镜，只调整镜头 12 的动作与前后切点。交文字候选，这轮先不生成视频。
```

项目从 [G0 锁定卡](G0-创意与立项/模板/项目规格与锁定卡.md)接续。按卡上制作档位执行，作品和阶段记录保存到当前项目工作区。
