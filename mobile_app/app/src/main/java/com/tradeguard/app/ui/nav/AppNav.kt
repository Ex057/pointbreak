package com.tradeguard.app.ui.nav

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CandlestickChart
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Route
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.tradeguard.app.ui.chart.ChartScreen
import com.tradeguard.app.ui.chart.ChartViewModel
import com.tradeguard.app.ui.markets.MarketsScreen
import com.tradeguard.app.ui.outlook.OutlookScreen
import com.tradeguard.app.ui.strategy.StrategyScreen
import androidx.lifecycle.viewmodel.compose.viewModel

private enum class Tab(val route: String, val label: String, val icon: ImageVector) {
    Markets("markets", "Markets", Icons.Filled.List),
    Chart("chart", "Chart", Icons.Filled.CandlestickChart),
    Outlook("outlook", "Outlook", Icons.Filled.Visibility),
    Strategy("strategy", "Strategy", Icons.Filled.Route),
}

@Composable
fun AppNav() {
    val nav = rememberNavController()
    val vm: ChartViewModel = viewModel()
    val backStack by nav.currentBackStackEntryAsState()
    val current = backStack?.destination

    Scaffold(
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach { tab ->
                    val selected = current?.hierarchy?.any { it.route == tab.route } == true
                    NavigationBarItem(
                        selected = selected,
                        onClick = {
                            nav.navigate(tab.route) {
                                popUpTo(nav.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(tab.icon, contentDescription = tab.label) },
                        label = { Text(tab.label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = nav,
            startDestination = Tab.Markets.route,
            modifier = Modifier.padding(padding),
        ) {
            composable(Tab.Markets.route) { MarketsScreen(vm) }
            composable(Tab.Chart.route) { ChartScreen(vm) }
            composable(Tab.Outlook.route) { OutlookScreen(vm) }
            composable(Tab.Strategy.route) { StrategyScreen(vm) }
        }
    }
}
