<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await api.exceptionHops()
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div class="page-card">
    <h1>异常轴</h1>
    <p class="lead">
      异常 event_code 集合内的轨迹点独立序列，库内 axis='exception' 落库，
      不进入正式有效时间线、不参与对外投影。
    </p>
    <p v-if="err" class="muted" style="color:var(--danger)">{{ err }}</p>
    <table class="table">
      <thead>
        <tr><th>id</th><th>运单</th><th>event_ts</th><th>地点</th><th>事件码</th><th>集合快照</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in rows" :key="r.id">
          <td>{{ r.id }}</td>
          <td>{{ r.waybill_id }}</td>
          <td>{{ r.event_ts }}</td>
          <td>{{ r.location_code }}</td>
          <td><span class="code-chip">{{ r.event_code }}</span></td>
          <td>v{{ r.exc_set_version }}</td>
        </tr>
        <tr v-if="!rows.length"><td colspan="6" class="muted">暂无异常轴点</td></tr>
      </tbody>
    </table>
  </div>
</template>
