"""LLM Context Compression for reduced token usage and faster responses.

Compresses context by:
- Schema minification (table(col:type) format)
- Data sampling and summarization
- Insight deduplication and ranking
- Removing redundant information
"""

from typing import Dict, Any, List, Optional
import json
import logging

logger = logging.getLogger(__name__)



class LLMContextCompressor:
    """Compress context to reduce LLM token usage and improve speed
    
    Usage:
        compressor = LLMContextCompressor(max_tokens=8000)
        
        # Compress schema
        compact_schema = compressor.compress_schema_context(
            schema, 
            relevant_tables=['sales_order', 'customer']
        )
        
        # Compress data results
        compact_data = compressor.compress_data_results(data, max_rows=15)
        
        # Compress insights
        compact_insights = compressor.compress_insights(insights, max_insights=6)
    """
    
    def __init__(self, max_tokens: int = 8000, target_tokens: int = 6000):
        self.max_tokens = max_tokens
        self.target_tokens = target_tokens
        # Approximate tokens per char (rough estimate for mixed content)
        self.tokens_per_char = 0.25
        
        # Stats
        self.stats = {
            'original_chars': 0,
            'compressed_chars': 0,
            'compression_count': 0,
        }
    
    def compress_schema_context(
        self, 
        schema: Dict[str, Any],
        relevant_tables: List[str] = None
    ) -> str:
        """Compress schema to only relevant tables
        
        Args:
            schema: Full database schema
            relevant_tables: List of table names to include (None = all)
            
        Returns:
            Compressed schema string
        """
        if relevant_tables:
            filtered = {
                k: v for k, v in schema.items() 
                if k in relevant_tables
            }
        else:
            filtered = schema
        
        # Compact representation: table(col:type,col:type)
        compressed = []
        for table, columns in filtered.items():
            if isinstance(columns, list):
                # List of column dicts
                col_parts = []
                for col in columns:
                    if isinstance(col, dict):
                        name = col.get('name', col.get('column_name', '?'))
                        dtype = col.get('type', col.get('data_type', 'unknown'))[:3]
                        col_parts.append(f"{name}:{dtype}")
                col_str = ','.join(col_parts)
            elif isinstance(columns, dict):
                # Dict of columns
                col_parts = [f"{k}:{v[:3] if isinstance(v, str) else 'unk'}" 
                            for k, v in columns.items()]
                col_str = ','.join(col_parts)
            else:
                col_str = str(columns)[:50]
            
            compressed.append(f"{table}({col_str})")
        
        result = '\n'.join(compressed)
        self._track_compression(sum(len(str(v)) for v in schema.values()), len(result))
        
        return result
    
    def compress_data_results(
        self,
        data: List[Dict],
        max_rows: int = 20,
        include_summary: bool = True
    ) -> str:
        """Compress query results for LLM consumption
        
        Args:
            data: Query results
            max_rows: Maximum rows to include
            include_summary: Include summary statistics
            
        Returns:
            Compressed JSON string
        """
        if not data:
            return "No data"
        
        original_len = len(str(data))
        
        # Sample data
        sample = data[:max_rows]
        
        # Create compact representation
        if include_summary and len(data) > 5:
            # Add summary statistics
            summary = self._calculate_summary(data)
            
            compact = {
                'count': len(data),
                'summary': summary,
                'sample': sample
            }
        else:
            compact = sample
        
        # Compact JSON (no indentation)
        result = json.dumps(compact, default=str, separators=(',', ':'))
        self._track_compression(original_len, len(result))
        
        return result
    
    def compress_insights(
        self,
        insights: List[Dict],
        max_insights: int = 8
    ) -> List[Dict]:
        """Compress insights to most valuable ones
        
        Args:
            insights: List of insight dicts
            max_insights: Maximum insights to return
            
        Returns:
            Compressed insights list
        """
        if not insights:
            return []
        
        # Sort by importance (warning/negative first, then by confidence)
        def importance_key(i):
            type_order = {'warning': 0, 'negative': 1, 'positive': 2, 'neutral': 3}
            return (
                type_order.get(i.get('type', 'neutral'), 3),
                -i.get('confidence', 0.5)
            )
        
        sorted_insights = sorted(insights, key=importance_key)
        
        # Take top N
        top_insights = sorted_insights[:max_insights]
        
        # Compress each insight
        compressed = []
        for insight in top_insights:
            compact = {
                't': insight.get('title', '')[:80],  # Truncate title
                'ty': insight.get('type', 'neutral')[:1].upper(),  # W/N/P/U
                'n': self._extract_numbers(insight.get('body', '')),
                'c': round(insight.get('confidence', 0.5), 2)
            }
            compressed.append(compact)
        
        return compressed
    
    def compress_sql_query(self, query: str, max_length: int = 500) -> str:
        """Compress SQL query by removing excess whitespace and comments
        
        Args:
            query: SQL query text
            max_length: Maximum length
            
        Returns:
            Compressed query
        """
        import re
        
        # Remove comments
        query = re.sub(r'--.*$', '', query, flags=re.MULTILINE)
        query = re.sub(r'/\*.*?\*/', '', query, flags=re.DOTALL)
        
        # Normalize whitespace
        query = ' '.join(query.split())
        
        # Truncate if too long
        if len(query) > max_length:
            query = query[:max_length-3] + '...'
        
        return query
    
    def compress_agent_context(
        self,
        context: Dict[str, Any],
        max_size_kb: int = 50
    ) -> Dict[str, Any]:
        """Compress full agent context object
        
        Args:
            context: Full agent context
            max_size_kb: Maximum size in KB
            
        Returns:
            Compressed context
        """
        compressed = {}
        
        # Always keep these keys (critical)
        critical_keys = ['trace_id', 'query_context', 'intent_mode']
        for key in critical_keys:
            if key in context:
                compressed[key] = context[key]
        
        # Compress large fields
        if 'schema_context' in context:
            compressed['schema'] = self.compress_schema_context(
                context['schema_context'],
                context.get('relevant_tables')
            )
        
        if 'execution_context' in context:
            exec_ctx = context['execution_context']
            if 'results' in exec_ctx:
                compressed['results'] = self.compress_data_results(
                    exec_ctx['results'],
                    max_rows=10
                )
            else:
                compressed['exec'] = {k: v for k, v in exec_ctx.items() 
                                     if k != 'full_results'}
        
        if 'insight_context' in context:
            insights = context['insight_context'].get('insights', [])
            compressed['insights'] = self.compress_insights(insights, 6)
        
        # Remove signal/drift details if too large
        for key in ['signal_context', 'drift_context']:
            if key in context:
                ctx = context[key]
                # Only keep summary
                compressed[key[:3]] = {
                    'count': len(ctx.get('signals', [])),
                    'severity': ctx.get('max_severity', 'low')
                }
        
        return compressed
    
    def _calculate_summary(self, data: List[Dict]) -> Dict:
        """Calculate summary statistics for numeric columns"""
        
        if not data:
            return {}
        
        summary = {}
        
        # Analyze first row to find columns
        first_row = data[0]
        
        for col in first_row.keys():
            values = []
            for row in data:
                val = row.get(col)
                if val is not None:
                    try:
                        values.append(float(val))
                    except (ValueError, TypeError):
                        pass
            
            if values:
                # Numeric column
                summary[col] = {
                    'min': round(min(values), 2),
                    'max': round(max(values), 2),
                    'avg': round(sum(values) / len(values), 2),
                    'sum': round(sum(values), 2),
                }
            else:
                # Categorical - count unique
                uniques = set(str(row.get(col)) for row in data if row.get(col))
                summary[col] = {
                    'uniq': len(uniques),
                    'vals': list(uniques)[:3]
                }
        
        return summary
    
    def _extract_numbers(self, text: str) -> List[str]:
        """Extract numeric values from text with units"""
        import re
        
        # Pattern: optional ₹, digits with optional commas, optional decimal, optional unit
        pattern = r'₹?[\d,]+(?:\.\d+)?(?:\s*(?:Cr|L|K|%|pcs|units))?'
        numbers = re.findall(pattern, text)
        
        # Return unique, limited
        seen = set()
        unique = []
        for n in numbers:
            if n not in seen and len(unique) < 4:
                seen.add(n)
                unique.append(n)
        
        return unique
    
    def _track_compression(self, original: int, compressed: int):
        """Track compression statistics"""
        self.stats['original_chars'] += original
        self.stats['compressed_chars'] += compressed
        self.stats['compression_count'] += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get compression statistics"""
        orig = self.stats['original_chars']
        comp = self.stats['compressed_chars']
        
        return {
            'total_compressions': self.stats['compression_count'],
            'original_chars': orig,
            'compressed_chars': comp,
            'avg_compression_ratio': (orig - comp) / orig if orig > 0 else 0,
            'estimated_tokens_saved': int((orig - comp) * self.tokens_per_char),
        }
    
    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            'original_chars': 0,
            'compressed_chars': 0,
            'compression_count': 0,
        }
