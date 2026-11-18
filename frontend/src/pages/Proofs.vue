<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api.js'

const statusRows = ref([])
const axisRows = ref([])

onMounted(async () => {
  const [sp, ap] = await Promise.all([api.proofs(), api.axisProofs()])
  statusRows.value = sp
  axisRows.value = ap
})
</script>

<template>
  <div class="page-card">
    <h1>证明</h1>
    <p class="lead">状态投影证明与分轴证明，均与正式有效轴末 hop 同源；异常轴点永不参与投影。</p>

    <h2 class="section-title">分轴证明（运单 · 正式末 hop · 异常点数 · 投影值 · 异常集合版本）</h2>
    <table class="table">
      <thead>
        <tr><th>id</th><th>运单</th><th>正式末 hop</th><th>末 hop 码</th><th>异常点数</th><th>投影值</th><th>集合版本</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in axisRows" :key="'a' + r.id">
          <td>{{ r.id }}</td>
          <td>{{ r.waybill_id }}</td>
          <td>{{ r.primary_last_hop_id || '—' }}</td>
          <td>{{ r.primary_last_code || '—' }}</td>
          <td><span class="exc-badge">{{ r.exception_count }}</span></td>
          <td>{{ r.proj_value }}</td>
          <td>v{{ r.exc_set_version }}</td>
          <td>{{ r.created_at }}</td>
        </tr>
        <tr v-if="!axisRows.length"><td colspan="8" class="muted">暂无分轴证明</td></tr>
      </tbody>
    </table>

    <h2 class="section-title">状态投影证明</h2>
    <table class="table">
      <thead>
        <tr><th>id</th><th>运单</th><th>末 hop</th><th>映射码</th><th>投影</th><th>时间</th></tr>
      </thead>
      <tbody>
        <tr v-for="r in statusRows" :key="'s' + r.id">
          <td>{{ r.id }}</td>
          <td>{{ r.waybill_id }}</td>
          <td>{{ r.last_hop_id }}</td>
          <td>{{ r.mapped_code }}</td>
          <td>{{ r.proj_value }}</td>
          <td>{{ r.created_at }}</td>
        </tr>
        <tr v-if="!statusRows.length"><td colspan="6" class="muted">暂无证明</td></tr>
      </tbody>
    </table>
  </div>
</template>
