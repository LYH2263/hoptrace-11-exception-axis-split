<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api.js'

const waybills = ref([])
const selected = ref(null)
const detail = ref(null)
const overview = ref(null)
const liveProof = ref(null)
const err = ref('')

async function loadList() {
  overview.value = await api.overview()
  waybills.value = await api.waybills()
  if (!selected.value && waybills.value.length) {
    selected.value = waybills.value[0].id
  }
}

async function loadDetail(id) {
  if (!id) return
  const [tl, proof] = await Promise.all([
    api.timeline(id),
    api.waybillAxisProof(id).catch(() => null),
  ])
  detail.value = tl
  liveProof.value = proof
}

onMounted(async () => {
  try {
    await loadList()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(selected, (id) => {
  loadDetail(id).catch((e) => { err.value = String(e.message || e) })
})

const hops = computed(() => detail.value?.hops || [])
const exceptionHops = computed(() => detail.value?.exception_hops || [])
const proof = computed(() => detail.value?.proof)
const splitProof = computed(() => detail.value?.axis_split_proof)
const excSet = computed(() => detail.value?.exception_set || overview.value?.exception_set)
</script>

<template>
  <div>
    <p v-if="err" class="muted">{{ err }}</p>
    <div class="stat-row" v-if="overview">
      <div class="stat"><b>{{ overview.waybills }}</b><span>运单</span></div>
      <div class="stat"><b>{{ overview.hops_primary }}</b><span>正式轴 hop</span></div>
      <div class="stat stat-danger"><b>{{ overview.hops_exception ?? 0 }}</b><span>异常轴 hop</span></div>
      <div class="stat"><b>{{ overview.hops_sidecar }}</b><span>旁路</span></div>
      <div class="stat"><b>{{ overview.quarantine }}</b><span>隔离</span></div>
      <div class="stat" v-if="overview.exception_set">
        <b>v{{ overview.exception_set.version }}</b><span>异常集合</span>
      </div>
    </div>

    <div class="tri">
      <aside class="pane waybill-pane">
        <h2>运单索引</h2>
        <button
          v-for="w in waybills"
          :key="w.id"
          class="wb-item"
          :class="{ active: selected === w.id }"
          @click="selected = w.id"
        >
          <span class="id">{{ w.id }}</span>
          <span class="title">{{ w.title }}</span>
          <span class="st">{{ w.status_proj }}</span>
        </button>
      </aside>

      <section class="pane axis-pane">
        <div class="axis-head">
          <h1>{{ detail?.waybill?.title || '时间线' }}</h1>
          <span>{{ selected }}</span>
        </div>

        <h2 class="axis-title axis-title-primary">正式有效轴（对外投影唯一来源）</h2>
        <div class="v-axis" v-if="hops.length">
          <div class="hop-node" v-for="h in hops" :key="h.id">
            <div class="ts">{{ h.event_ts }}</div>
            <div class="code">{{ h.event_code }}</div>
            <div class="loc">{{ h.location_code }} · {{ h.id }} · 集合快照 v{{ h.exc_set_version }}</div>
          </div>
        </div>
        <p v-else class="muted">无正式轨迹点（投影保持原值，异常点不得替代）</p>

        <h2 class="axis-title axis-title-exc">
          异常轴（独立可查序列）<span class="exc-count">{{ exceptionHops.length }} 点</span>
        </h2>
        <div class="v-axis v-axis-exc" v-if="exceptionHops.length">
          <div class="hop-node hop-node-exc" v-for="h in exceptionHops" :key="h.id">
            <div class="ts">{{ h.event_ts }}</div>
            <div class="code">{{ h.event_code }}</div>
            <div class="loc">{{ h.location_code }} · {{ h.id }} · 集合快照 v{{ h.exc_set_version }}</div>
          </div>
        </div>
        <p v-else class="muted">无异常轨迹点</p>
      </section>

      <aside class="pane inspect-pane">
        <h2>检查器</h2>
        <dl class="kv" v-if="detail?.waybill">
          <dt>运单</dt><dd>{{ detail.waybill.id }}</dd>
          <dt>投影状态</dt><dd>{{ detail.waybill.status_proj }}</dd>
          <dt>创建</dt><dd>{{ detail.waybill.created_at }}</dd>
        </dl>

        <h2>分轴证明（只读）</h2>
        <dl class="kv" v-if="liveProof">
          <dt>运单</dt><dd>{{ liveProof.waybill_id }}</dd>
          <dt>正式末 hop</dt><dd>{{ liveProof.primary_last_hop_id || '—（正式轴空）' }}</dd>
          <dt>末 hop 码</dt><dd>{{ liveProof.primary_last_event_code || '—' }}</dd>
          <dt>异常点数</dt><dd>{{ liveProof.exception_count }}</dd>
          <dt>投影值</dt><dd>{{ liveProof.proj_value }}</dd>
          <dt>集合版本</dt><dd>v{{ liveProof.exc_set_version }}</dd>
          <dt>物化互证</dt>
          <dd :class="liveProof.consistent ? 'proof-ok' : 'proof-bad'">
            {{ liveProof.consistent ? '一致' : '无物化证明/不一致' }}
          </dd>
        </dl>
        <p v-else class="muted">暂无分轴证明</p>

        <h2>状态证明</h2>
        <dl class="kv" v-if="proof">
          <dt>末 hop</dt><dd>{{ proof.last_hop_id }}</dd>
          <dt>映射码</dt><dd>{{ proof.mapped_code }}</dd>
          <dt>投影值</dt><dd>{{ proof.proj_value }}</dd>
          <dt>写入</dt><dd>{{ proof.created_at }}</dd>
        </dl>
        <p v-else class="muted">暂无证明</p>

        <h2>异常集合</h2>
        <dl class="kv" v-if="excSet">
          <dt>当前版本</dt><dd>v{{ excSet.version }}</dd>
          <dt>异常码</dt>
          <dd>
            <span class="code-chip" v-for="c in excSet.codes" :key="c">{{ c }}</span>
          </dd>
        </dl>
      </aside>

      <footer class="pane dot-rail">
        <span class="label">正式 HOP</span>
        <template v-for="(h, i) in hops" :key="h.id">
          <span class="dot on"></span>
          <span v-if="i < hops.length - 1" class="dot-line"></span>
        </template>
        <span v-if="!hops.length" class="muted rail-empty">正式轴无点</span>
        <span class="label label-exc">异常 HOP</span>
        <template v-for="(h, i) in exceptionHops" :key="h.id">
          <span class="dot dot-exc"></span>
          <span v-if="i < exceptionHops.length - 1" class="dot-line dot-line-exc"></span>
        </template>
        <span v-if="!exceptionHops.length" class="muted rail-empty">异常轴无点</span>
      </footer>
    </div>
  </div>
</template>
