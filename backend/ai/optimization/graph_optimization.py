"""Graph Data Optimization for faster rendering.

Optimizes graph data by:
- Data sampling for large datasets
- Time-series aggregation
- Category grouping (top N + others)
- Data point limiting
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class GraphOptimizationConfig:
    """Configuration for graph optimization"""
    max_points: int = 100
    enable_sampling: bool = True
    enable_aggregation: bool = True
    aggregation_threshold: int = 500  # Aggregate if more than this
    sampling_threshold: int = 100   # Sample if between 100-500


class GraphOptimizationEngine:
    """Optimize graph data for fast rendering
    
    Usage:
        optimizer = GraphOptimizationEngine()
        
        optimized = optimizer.optimize_graph_data(
            graph_type='line',
            data=large_dataset,
            max_points=100
        )
        
        # Use optimized['data'] for rendering
    """
    
    def __init__(self, config: Optional[GraphOptimizationConfig] = None):
        self.config = config or GraphOptimizationConfig()
        
        # Optimization strategies by graph type
        self.strategies = {
            'line': self._optimize_line_chart,
            'area': self._optimize_line_chart,
            'bar': self._optimize_bar_chart,
            'horizontalBar': self._optimize_bar_chart,
            'pie': self._optimize_pie_chart,
            'doughnut': self._optimize_pie_chart,
            'scatter': self._optimize_scatter_chart,
            'default': self._optimize_default,
        }
    
    def optimize_graph_data(
        self,
        graph_type: str,
        data: List[Dict],
        x_column: str = 'label',
        y_column: str = 'value',
        max_points: Optional[int] = None
    ) -> Dict[str, Any]:
        """Optimize graph data for fast rendering
        
        Args:
            graph_type: Type of graph (line, bar, pie, etc.)
            data: Raw graph data
            x_column: Column for X-axis/labels
            y_column: Column for Y-axis/values
            max_points: Override max points
            
        Returns:
            Optimization metadata and optimized data
        """
        if not data:
            return {
                'data': [],
                'original_count': 0,
                'rendered_count': 0,
                'strategy': 'none',
                'optimization_applied': False
            }
        
        original_count = len(data)
        target_points = max_points or self.config.max_points
        
        # Determine optimization strategy
        if original_count <= target_points:
            strategy = 'full'
            optimized_data = data
        elif original_count <= target_points * 5:
            strategy = 'sampling'
            optimized_data = self._sample_data(data, target_points)
        else:
            strategy = 'aggregation'
            optimizer = self.strategies.get(graph_type, self._optimize_default)
            optimized_data = optimizer(data, x_column, y_column, target_points)
        
        return {
            'data': optimized_data,
            'original_count': original_count,
            'rendered_count': len(optimized_data),
            'strategy': strategy,
            'optimization_applied': strategy != 'full',
            'reduction_ratio': original_count / len(optimized_data) if optimized_data else 1,
            'x_column': x_column,
            'y_column': y_column,
        }
    
    def _sample_data(self, data: List[Dict], target_points: int) -> List[Dict]:
        """Sample data points evenly"""
        
        if len(data) <= target_points:
            return data
        
        step = len(data) / target_points
        sampled = []
        
        for i in range(target_points):
            idx = int(i * step)
            sampled.append(data[min(idx, len(data) - 1)])
        
        # Always include last point
        if data[-1] not in sampled:
            sampled[-1] = data[-1]
        
        return sampled
    
    def _optimize_line_chart(
        self,
        data: List[Dict],
        x_col: str,
        y_col: str,
        target_points: int
    ) -> List[Dict]:
        """Optimize line/area charts with time-series aggregation"""
        
        # For time-series, use bucketing
        bucket_size = len(data) // target_points
        
        aggregated = []
        for i in range(0, len(data), bucket_size):
            bucket = data[i:i + bucket_size]
            
            # Aggregate Y values
            y_values = []
            for row in bucket:
                val = row.get(y_col)
                if val is not None:
                    try:
                        y_values.append(float(val))
                    except:
                        pass
            
            if y_values:
                aggregated.append({
                    x_col: bucket[0].get(x_col, ''),
                    f'{y_col}_avg': round(sum(y_values) / len(y_values), 2),
                    f'{y_col}_min': round(min(y_values), 2),
                    f'{y_col}_max': round(max(y_values), 2),
                    '_count': len(bucket),
                    '_original_indices': f"{i}-{min(i+bucket_size, len(data))-1}"
                })
        
        return aggregated
    
    def _optimize_bar_chart(
        self,
        data: List[Dict],
        x_col: str,
        y_col: str,
        target_points: int
    ) -> List[Dict]:
        """Optimize bar charts with top N + others grouping"""
        
        # Sort by value descending
        sorted_data = sorted(
            data,
            key=lambda x: float(x.get(y_col, 0)) if x.get(y_col) else 0,
            reverse=True
        )
        
        # Keep top N-1, aggregate rest into "Others"
        top_n = sorted_data[:target_points - 1]
        others = sorted_data[target_points - 1:]
        
        if others:
            # Sum up others
            others_sum = sum(
                float(row.get(y_col, 0)) 
                for row in others 
                if row.get(y_col) is not None
            )
            
            # Count in others
            others_count = len(others)
            
            top_n.append({
                x_col: f'_Others ({others_count})',
                y_col: round(others_sum, 2),
                '_is_aggregated': True,
                '_count': others_count
            })
        
        return top_n
    
    def _optimize_pie_chart(
        self,
        data: List[Dict],
        x_col: str,
        y_col: str,
        target_points: int
    ) -> List[Dict]:
        """Optimize pie/doughnut charts with small slice grouping"""
        
        # Calculate total
        total = sum(
            float(row.get(y_col, 0)) 
            for row in data 
            if row.get(y_col) is not None
        )
        
        if total == 0:
            return data[:target_points]
        
        # Calculate percentages
        with_pct = []
        for row in data:
            val = float(row.get(y_col, 0)) if row.get(y_col) else 0
            pct = (val / total) * 100
            with_pct.append({
                **row,
                '_pct': pct
            })
        
        # Sort by percentage
        sorted_data = sorted(with_pct, key=lambda x: x['_pct'], reverse=True)
        
        # Keep slices > 2%, group rest
        significant = [r for r in sorted_data if r['_pct'] > 2][:target_points-1]
        small = [r for r in sorted_data if r['_pct'] <= 2 or r not in significant]
        
        if small:
            small_total = sum(r.get(y_col, 0) for r in small)
            small_pct = sum(r['_pct'] for r in small)
            
            significant.append({
                x_col: f'_Others ({len(small)} items, {small_pct:.1f}%)',
                y_col: round(small_total, 2),
                '_is_aggregated': True,
                '_pct': small_pct
            })
        
        # Remove temporary percentage field
        for row in significant:
            row.pop('_pct', None)
        
        return significant
    
    def _optimize_scatter_chart(
        self,
        data: List[Dict],
        x_col: str,
        y_col: str,
        target_points: int
    ) -> List[Dict]:
        """Optimize scatter charts with density-based sampling"""
        
        # For scatter, use stratified sampling to preserve distribution
        if len(data) <= target_points:
            return data
        
        # Sort by x value
        sorted_data = sorted(
            data,
            key=lambda x: float(x.get(x_col, 0)) if x.get(x_col) else 0
        )
        
        # Sample evenly across the range
        step = len(sorted_data) / target_points
        sampled = []
        
        for i in range(target_points):
            idx = int(i * step)
            sampled.append(sorted_data[min(idx, len(sorted_data) - 1)])
        
        return sampled
    
    def _optimize_default(
        self,
        data: List[Dict],
        x_col: str,
        y_col: str,
        target_points: int
    ) -> List[Dict]:
        """Default optimization - simple sampling"""
        return self._sample_data(data, target_points)
    
    def optimize_all_graphs(
        self,
        graphs: List[Dict],
        max_points: Optional[int] = None
    ) -> List[Dict]:
        """Optimize all graphs in a report
        
        Args:
            graphs: List of graph definitions with 'data' and 'type'
            max_points: Override max points
            
        Returns:
            Optimized graph definitions
        """
        optimized = []
        
        for graph in graphs:
            graph_type = graph.get('type', 'bar')
            data = graph.get('data', [])
            
            # Determine columns from first row
            if data:
                keys = list(data[0].keys())
                x_col = keys[0] if keys else 'label'
                y_col = keys[1] if len(keys) > 1 else 'value'
            else:
                x_col, y_col = 'label', 'value'
            
            optimization = self.optimize_graph_data(
                graph_type,
                data,
                x_col,
                y_col,
                max_points
            )
            
            # Create optimized graph
            opt_graph = {
                **graph,
                'data': optimization['data'],
                '_optimization': {
                    'strategy': optimization['strategy'],
                    'original_count': optimization['original_count'],
                    'reduction_ratio': round(optimization['reduction_ratio'], 2),
                }
            }
            
            optimized.append(opt_graph)
        
        return optimized
