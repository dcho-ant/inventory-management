<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="lastOrder" class="success-banner">
      {{ t('restocking.success', { n: lastOrder.order_number }) }}
      <router-link to="/orders">{{ t('restocking.viewInOrders') }}</router-link>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>

      <!-- Budget card -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget') }}</h3>
        </div>
        <input
          type="range"
          v-model.number="budget"
          :min="0"
          :max="maxBudget"
          :step="100"
          class="budget-slider"
        />
        <div class="budget-amount">{{ formatCurrency(budget, currentCurrency) }}</div>
        <div class="budget-bar">
          <div
            class="budget-bar-fill"
            :class="{ over: overBudget }"
            :style="{ width: budgetUsedPct + '%' }"
          ></div>
        </div>
        <div class="budget-summary">
          <span>{{ t('restocking.selectedOf', { selected: selection.size, total: recs.length }) }}</span>
          <span>{{ formatCurrency(selectedTotal, currentCurrency) }}</span>
        </div>
        <button class="reset-btn" @click="resetToRecommended">{{ t('restocking.reset') }}</button>
      </div>

      <!-- Recommendations card -->
      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }} ({{ recs.length }})</h3>
        </div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th class="col-check">
                  <input type="checkbox" :checked="allSelected" @change="toggleAll" />
                </th>
                <th>{{ t('inventory.table.sku') }}</th>
                <th>{{ t('inventory.table.itemName') }}</th>
                <th>{{ t('demand.table.trend') }}</th>
                <th>{{ t('restocking.forecastGap') }}</th>
                <th>{{ t('restocking.unitCost') }}</th>
                <th>{{ t('restocking.qty') }}</th>
                <th>{{ t('restocking.lineCost') }}</th>
                <th>{{ t('restocking.leadTime') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in recs" :key="r.sku">
                <td class="col-check">
                  <input
                    type="checkbox"
                    :checked="selection.has(r.sku)"
                    @change="toggle(r)"
                  />
                </td>
                <td class="sku">{{ r.sku }}</td>
                <td>{{ r.name }}</td>
                <td>
                  <span :class="['badge', r.trend]">{{ t(`trends.${r.trend}`) }}</span>
                </td>
                <td>+{{ r.suggested_quantity }}</td>
                <td>
                  {{ currencySymbol }}{{ r.unit_cost.toFixed(2) }}
                  <span v-if="r.cost_source === 'fallback'" class="est-chip">{{ t('restocking.estimated') }}</span>
                </td>
                <td>
                  <input
                    type="number"
                    min="0"
                    :max="r.forecasted_demand"
                    :disabled="!selection.has(r.sku)"
                    :value="selection.get(r.sku)?.quantity ?? 0"
                    @input="setQty(r, $event.target.valueAsNumber)"
                    class="qty-input"
                  />
                </td>
                <td>{{ currencySymbol }}{{ lineCost(r).toLocaleString() }}</td>
                <td>{{ r.lead_time_days }} {{ t('restocking.days') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </div>

    <!-- Sticky submit bar -->
    <div class="submit-bar">
      <span>{{ t('restocking.total') }}: <strong>{{ formatCurrency(selectedTotal, currentCurrency) }}</strong></span>
      <span v-if="overBudget" class="over-budget">{{ t('restocking.overBudget') }}</span>
      <button
        class="btn-primary"
        :disabled="submitting || selection.size === 0 || overBudget"
        @click="placeOrder"
      >
        {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
      </button>
    </div>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency } = useI18n()
    const currencySymbol = computed(() => currentCurrency.value === 'JPY' ? '¥' : '$')

    const loading = ref(true)
    const error = ref(null)
    const submitting = ref(false)
    const recs = ref([])
    const budget = ref(0)
    const selection = ref(new Map()) // sku -> { quantity, unit_cost, name }
    const lastOrder = ref(null)

    const maxBudget = computed(() => {
      const sum = recs.value.reduce((s, r) => s + r.line_cost, 0)
      return Math.max(Math.ceil(sum / 100) * 100, 1000)
    })

    const selectedTotal = computed(() => {
      let total = 0
      for (const v of selection.value.values()) total += v.quantity * v.unit_cost
      return Math.round(total * 100) / 100
    })

    const overBudget = computed(() => selectedTotal.value > budget.value)

    const budgetUsedPct = computed(() => {
      if (budget.value === 0) return 0
      return Math.min(100, (selectedTotal.value / budget.value) * 100)
    })

    const allSelected = computed(() =>
      recs.value.length > 0 && recs.value.every(r => selection.value.has(r.sku))
    )

    // Greedy fill: take full suggested_quantity if it fits, else partial.
    // Server already returns recs sorted by priority.
    const autoSelect = () => {
      const sel = new Map()
      let spent = 0
      for (const r of recs.value) {
        const full = r.suggested_quantity * r.unit_cost
        if (spent + full <= budget.value) {
          sel.set(r.sku, { quantity: r.suggested_quantity, unit_cost: r.unit_cost, name: r.name })
          spent += full
        } else {
          const affordable = Math.floor((budget.value - spent) / r.unit_cost)
          if (affordable > 0) {
            sel.set(r.sku, { quantity: affordable, unit_cost: r.unit_cost, name: r.name })
            spent += affordable * r.unit_cost
          }
        }
      }
      selection.value = sel
    }

    // Map mutations aren't deeply reactive — reassign after mutating.
    const toggle = (r) => {
      const next = new Map(selection.value)
      if (next.has(r.sku)) next.delete(r.sku)
      else next.set(r.sku, { quantity: r.suggested_quantity, unit_cost: r.unit_cost, name: r.name })
      selection.value = next
    }

    const toggleAll = () => {
      if (allSelected.value) { selection.value = new Map(); return }
      const next = new Map()
      for (const r of recs.value) next.set(r.sku, { quantity: r.suggested_quantity, unit_cost: r.unit_cost, name: r.name })
      selection.value = next
    }

    const setQty = (r, q) => {
      const clamped = Math.max(0, Math.min(r.forecasted_demand, Number.isFinite(q) ? q : 0))
      const next = new Map(selection.value)
      if (clamped === 0) next.delete(r.sku)
      else next.set(r.sku, { quantity: clamped, unit_cost: r.unit_cost, name: r.name })
      selection.value = next
    }

    const lineCost = (r) => {
      const v = selection.value.get(r.sku)
      return v ? Math.round(v.quantity * v.unit_cost * 100) / 100 : 0
    }

    const resetToRecommended = () => autoSelect()

    const load = async () => {
      try {
        loading.value = true
        error.value = null
        recs.value = await api.getRestockingRecommendations()
        budget.value = Math.round(maxBudget.value * 0.5 / 100) * 100
        autoSelect()
      } catch (e) {
        error.value = 'Failed to load recommendations: ' + e.message
        console.error(e)
      } finally {
        loading.value = false
      }
    }

    const placeOrder = async () => {
      submitting.value = true
      error.value = null
      try {
        const items = [...selection.value.entries()].map(([sku, v]) => ({
          sku, name: v.name, quantity: v.quantity, unit_cost: v.unit_cost
        }))
        lastOrder.value = await api.createRestockingOrder({ items, budget: budget.value })
        selection.value = new Map()
      } catch (e) {
        error.value = e.response?.data?.detail || e.message
      } finally {
        submitting.value = false
      }
    }

    watch(budget, autoSelect)
    onMounted(load)

    return {
      t, formatCurrency, currentCurrency, currencySymbol,
      loading, error, submitting, recs, budget, maxBudget, selection,
      selectedTotal, overBudget, budgetUsedPct, allSelected, lastOrder,
      toggle, toggleAll, setQty, lineCost, resetToRecommended, placeOrder
    }
  }
}
</script>

<style scoped>
.budget-slider {
  width: 100%;
  accent-color: #3b82f6;
  margin-bottom: 0.75rem;
}

.budget-amount {
  font-size: 1.75rem;
  font-weight: 600;
  color: #0f172a;
}

.budget-bar {
  height: 8px;
  background: #e2e8f0;
  border-radius: 4px;
  overflow: hidden;
  margin: 1rem 0;
}

.budget-bar-fill {
  height: 100%;
  background: #3b82f6;
  transition: width 0.15s;
}

.budget-bar-fill.over {
  background: #dc2626;
}

.budget-summary {
  display: flex;
  justify-content: space-between;
  font-size: 0.875rem;
  color: #64748b;
  margin-bottom: 1rem;
}

.reset-btn {
  background: transparent;
  border: 1px solid #e2e8f0;
  padding: 0.5rem 0.875rem;
  border-radius: 6px;
  font-size: 0.8125rem;
  cursor: pointer;
}

.reset-btn:hover {
  background: #f8fafc;
}

.col-check {
  width: 2.5rem;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  text-align: left;
  padding: 0.75rem;
  border-bottom: 1px solid #e2e8f0;
  font-size: 0.875rem;
}

th {
  font-weight: 600;
  color: #64748b;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

.sku {
  font-family: ui-monospace, monospace;
  font-size: 0.8125rem;
}

.qty-input {
  width: 5rem;
  padding: 0.375rem;
  border: 1px solid #e2e8f0;
  border-radius: 4px;
  text-align: right;
}

.qty-input:disabled {
  background: #f8fafc;
  color: #94a3b8;
}

.est-chip {
  font-size: 0.6875rem;
  background: #f1f5f9;
  color: #64748b;
  padding: 1px 6px;
  border-radius: 3px;
  margin-left: 6px;
}

.badge.increasing {
  background: #dcfce7;
  color: #166534;
}

.badge.stable {
  background: #dbeafe;
  color: #1e40af;
}

.badge.decreasing {
  background: #fef3c7;
  color: #92400e;
}

.submit-bar {
  position: sticky;
  bottom: 0;
  background: white;
  border-top: 1px solid #e2e8f0;
  padding: 1rem 1.25rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  margin-top: 1.5rem;
}

.over-budget {
  color: #dc2626;
  font-weight: 600;
  font-size: 0.875rem;
}

.btn-primary {
  background: #3b82f6;
  color: white;
  border: none;
  padding: 0.625rem 1.25rem;
  border-radius: 6px;
  font-weight: 500;
  cursor: pointer;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.success-banner {
  background: #dcfce7;
  border: 1px solid #86efac;
  color: #166534;
  padding: 0.875rem 1rem;
  border-radius: 6px;
  margin-bottom: 1rem;
}

.success-banner a {
  color: #166534;
  font-weight: 600;
  text-decoration: underline;
  margin-left: 0.5rem;
}
</style>
