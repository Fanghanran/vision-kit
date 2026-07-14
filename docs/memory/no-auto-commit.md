---
name: no-auto-commit
description: 不要自动 git add/commit/push，等用户明确指示
metadata: 
  node_type: memory
  type: feedback
  originSessionId: eecfb2fb-e76a-46ec-be88-c25df381eb79
---

不要自动执行 git add、git commit、git push。修改完代码后等用户说"提交"或"推送"再操作。

**Why:** 用户想在提交前确认代码质量，不想被自动提交。
**How to apply:** 代码写完后只告诉用户改了什么，等用户指示再提交。
