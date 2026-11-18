<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const current = ref(null)
const versions = ref([])
const codesText = ref('')
const note = ref('')
const busy = ref(false)
const err = ref('')
const okMsg = ref('')

async function load() {
  const data = await api.exceptionSet()
  current.value = data.current
  versions.value = data.versions
  codesText.value = data.current.codes.join(', ')
}

onMounted(load)

async function save() {
  busy.value = true
  err.value = ''
  okMsg.value = ''
  try {
    const codes = codesText.value
      .split(/[,\n]/)
      .map((s) => s.trim())
      .filter(Boolean)
    const res = await api.saveExceptionSet(codes, note.value)
    okMsg.value = `已保存为新版本 v${res.current.version}；历史 hop 不回溯，仅影响此后装载。`
    note.value = ''
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-card">
    <h1>异常 event_code 集合</h1>
    <p class="lead">
      集合内的轨迹点必须落入异常轴，不得进入正式有效时间线；对外投影只由正式轴末 hop 派生。
      保存即生成<strong>新版本落库</strong>（仅追加），历史点按行内快照版本解释，<strong>不回溯改写历史正式轴</strong>。
    </p>

    <div class="form-block" v-if="current">
      <label class="field-label">当前版本：v{{ current.version }}（{{ current.created_at }}）</label>
      <div class="chip-row">
        <span class="code-chip" v-for="c in current.codes" :key="c">{{ c }}</span>
      </div>

      <label class="field-label" for="codes">新版本异常码（逗号或换行分隔，至少一个）</label>
      <textarea
        id="codes"
        v-model="codesText"
        rows="4"
        class="code-input"
        placeholder="EXCEPTION, DAMAGED, LOST"
      ></textarea>

      <label class="field-label" for="note">变更备注（可选）</label>
      <input id="note" v-model="note" class="code-input note-input" placeholder="例如：新增 RETURNED 异常码" />

      <div class="btn-row">
        <button class="btn" :disabled="busy" @click="save">
          {{ busy ? '保存中…' : '保存异常集合新版本' }}
        </button>
      </div>
      <p v-if="err" class="muted" style="color:var(--danger)">{{ err }}</p>
      <p v-if="okMsg" class="muted" style="color:var(--mint)">{{ okMsg }}</p>
    </div>

    <h2 class="section-title">历史版本（仅追加，可审计）</h2>
    <table class="table">
      <thead>
        <tr><th>版本</th><th>异常码集合</th><th>备注</th><th>创建时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="v in versions" :key="v.version">
          <td>v{{ v.version }}<span v-if="current && v.version === current.version" class="cur-tag">当前</span></td>
          <td>
            <span class="code-chip" v-for="c in v.codes" :key="c">{{ c }}</span>
          </td>
          <td>{{ v.note }}</td>
          <td>{{ v.created_at }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
