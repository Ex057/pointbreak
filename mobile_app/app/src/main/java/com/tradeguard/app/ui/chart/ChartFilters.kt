package com.tradeguard.app.ui.chart

import com.tradeguard.app.data.model.Zone

/** How many priority buckets to reveal. Default KEY = high only, so the chart is clean. */
enum class Density { KEY, MORE, LOW, ALL }

/** Which zone layers are visible. FVG off by default — it's the noisy flood. */
data class LayerVis(
    val sr: Boolean = true,
    val supplyDemand: Boolean = true,
    val fvg: Boolean = false,
    val trendlines: Boolean = true,
)

fun Density.allows(priority: String?): Boolean = when (this) {
    Density.KEY -> priority == "high"
    Density.MORE -> priority == "high" || priority == "medium"
    Density.LOW -> priority == "high" || priority == "medium" || priority == "low"
    Density.ALL -> true
}

/**
 * Filter a layer's zones by visibility + density, then keep only the strongest
 * `cap` by priority score. Guarantees the chart never floods, whatever the filter.
 */
fun visibleZones(all: List<Zone>, on: Boolean, density: Density, cap: Int): List<Zone> {
    if (!on) return emptyList()
    return all.asSequence()
        .filter { density.allows(it.priority) }
        .sortedByDescending { it.priorityScore }
        .take(cap)
        .toList()
}
