<script setup>
import { ref } from 'vue'
import { api } from '../api.js'

const result = ref(null)
const busy = ref(false)
const err = ref('')

async function run() {
  busy.value = true
  err.value = ''
  try {
    result.value = await api.ingest()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page-card">
    <h1>装载</h1>
    <p class="lead">
      从本地夹具跑一批轨迹点装载。装载时按当前异常 event_code 集合分流：
      集合内的点入异常轴，其余入正式有效轴；单事务提交，分流失败整趟回滚（失败闭）。
    </p>
    <button class="btn" :disabled="busy" @click="run">{{ busy ? '装载中…' : '跑装载批次' }}</button>
    <p v-if="err" class="muted" style="margin-top:12px;color:var(--danger)">{{ err }}</p>

    <div v-if="result" class="ingest-report" style="margin-top:16px">
      <h2 class="section-title">
        装载结果
        <span :class="result.ok ? 'proof-ok' : 'proof-bad'">{{ result.ok ? '成功提交' : '失败闭回滚' }}</span>
      </h2>
      <template v-if="result.ok">
        <dl class="kv report-kv">
          <dt>批次</dt><dd>{{ result.report.batch_id }}</dd>
          <dt>输入点数</dt><dd>{{ result.report.input }}</dd>
          <dt>实际写入</dt><dd>{{ result.report.written }}</dd>
          <dt>→ 正式轴</dt><dd>{{ result.report.routed_primary }}</dd>
          <dt>→ 异常轴</dt><dd><span class="exc-badge">{{ result.report.routed_exception }}</span></dd>
          <dt>异常集合版本</dt><dd>v{{ result.report.exc_set_version }}</dd>
          <dt>集合内异常码</dt>
          <dd><span class="code-chip" v-for="c in result.report.exc_codes" :key="c">{{ c }}</span></dd>
        </dl>
      </template>
      <p v-else class="muted" style="color:var(--danger)">{{ result.error }}</p>
    </div>
  </div>
</template>
