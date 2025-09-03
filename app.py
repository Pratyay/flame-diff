#!/usr/bin/env python3
"""
Flame Graph Diff Tool - Backend Server
Author: Assistant
Description: A Flask web application for comparing flame graphs and visualizing differences
"""

from flask import Flask, render_template, request, jsonify
import re
from collections import defaultdict
from typing import Dict, List, Tuple
import os

app = Flask(__name__)

class FlameGraphParser:
    """Parser for collapsed flame graph files"""
    
    def __init__(self):
        self.stack_counts = defaultdict(int)
        self.total_samples = 0
    
    def normalize_stack_trace(self, stack_trace: str) -> str:
        """Normalize stack trace to group similar dynamic traces together"""
        # Split stack trace into individual frames
        frames = stack_trace.split(';')
        normalized_frames = []
        
        for frame in frames:
            # Remove flame graph annotations like [j], [i], etc.
            clean_frame = re.sub(r'\s*\[[a-zA-Z]\]\s*$', '', frame)
            clean_frame = re.sub(r'_+\s*$', '', clean_frame).strip()
            
            # Normalize common dynamic Java patterns
            normalized_frame = clean_frame
            
            # First, handle native library files with version numbers and hashes
            # e.g., snappy-1.1.4-6b5c8fd-1-8e7b-4e8-a4c5cc9d-libsnappyjava.so -> snappy-*-libsnappyjava.so
            if 'libsnappyjava.so' in normalized_frame:
                normalized_frame = re.sub(r'snappy-[^-]+-[^-]+-[^-]+-[^-]+-[^-]+-[^-]+-libsnappyjava\.so', 'snappy-*-libsnappyjava.so', normalized_frame)
            
            # More generic pattern for other native libraries with similar patterns
            normalized_frame = re.sub(r'(lib\w+)-(\d+\.\d+\.\d+)?-[a-f0-9]+-\d+-[a-f0-9]+-[a-f0-9]+-[a-f0-9]+-([^/]+\.so)', r'\1-*-\3', normalized_frame)
            
            # Then normalize hexadecimal identifiers in CGLib classes BEFORE number replacement
            # Replace hex patterns like 4e5e6, e4cee, ddbf4d with * 
            # Look for sequences of hex chars (a-f, 0-9) that are likely identifiers
            normalized_frame = re.sub(r'\$\$[a-f0-9]{3,}(?=\$|\.|$)', '$$*', normalized_frame)
            normalized_frame = re.sub(r'\$[a-f0-9]{3,}(?=\$|\.|$)', '$*', normalized_frame)
            
            # Finally apply generic number normalization for remaining cases
            # This catches GeneratedMethodAccessor123, Lambda$45, anonymous classes $12, etc.
            normalized_frame = re.sub(r'\d+', '*', normalized_frame)
            
            # 5. Line numbers in parentheses (optional - comment out if you want to keep them)
            # normalized_frame = re.sub(
            #     r'\([^)]*:\d+\)',
            #     '(*:*)',
            #     normalized_frame
            # )
            
            normalized_frames.append(normalized_frame)
        
        return ';'.join(normalized_frames)
    
    def parse_file(self, file_content: str) -> Dict[str, int]:
        """Parse a collapsed flame graph file and return stack counts"""
        stack_counts = defaultdict(int)
        total_samples = 0
        
        for line in file_content.strip().split('\n'):
            if not line.strip():
                continue
            
            # Extract count from the end of the line (space separated)
            parts = line.rsplit(' ', 1)
            if len(parts) != 2:
                continue
                
            try:
                stack_trace = parts[0].strip()
                count = int(parts[1].strip())
                
                # Normalize the stack trace before storing
                normalized_stack = self.normalize_stack_trace(stack_trace)
                stack_counts[normalized_stack] += count  # Use += to combine normalized stacks
                total_samples += count
            except ValueError:
                # Debug: print problematic lines
                print(f"Failed to parse line: {line}")
                continue
        
        print(f"Parsed {len(stack_counts)} unique stacks, total samples: {total_samples}")
        return stack_counts, total_samples

class FlameGraphDiffer:
    """Compare two flame graphs and generate diff results"""
    
    def __init__(self):
        pass
    
    def calculate_diff(self, old_stacks: Dict[str, int], new_stacks: Dict[str, int], 
                      old_total: int, new_total: int) -> Dict:
        """Calculate differences between two flame graphs"""
        
        # Get all unique stacks
        all_stacks = set(old_stacks.keys()) | set(new_stacks.keys())
        
        results = {
            'added': [],
            'removed': [],
            'increased': [],
            'decreased': [],
            'unchanged': [],
            'summary': {
                'old_total': old_total,
                'new_total': new_total,
                'total_change': new_total - old_total,
                'total_change_percent': ((new_total - old_total) / old_total * 100) if old_total > 0 else 0
            }
        }
        
        for stack in all_stacks:
            old_count = old_stacks.get(stack, 0)
            new_count = new_stacks.get(stack, 0)
            
            old_percent = (old_count / old_total * 100) if old_total > 0 else 0
            new_percent = (new_count / new_total * 100) if new_total > 0 else 0
            
            diff_count = new_count - old_count
            diff_percent = new_percent - old_percent
            
            stack_info = {
                'stack': stack,
                'old_count': old_count,
                'new_count': new_count,
                'old_percent': round(old_percent, 2),
                'new_percent': round(new_percent, 2),
                'diff_count': diff_count,
                'diff_percent': round(diff_percent, 2)
            }
            
            if old_count == 0 and new_count > 0:
                results['added'].append(stack_info)
            elif old_count > 0 and new_count == 0:
                results['removed'].append(stack_info)
            elif diff_count > 0:
                results['increased'].append(stack_info)
            elif diff_count < 0:
                results['decreased'].append(stack_info)
            else:
                results['unchanged'].append(stack_info)
        
        # Sort by absolute difference (most significant changes first)
        for category in ['added', 'removed', 'increased', 'decreased']:
            results[category].sort(key=lambda x: abs(x['diff_percent']), reverse=True)
        
        return results

# Initialize global objects
parser = FlameGraphParser()
differ = FlameGraphDiffer()

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')

@app.route('/api/diff', methods=['POST'])
def diff_flame_graphs():
    """API endpoint to compare two flame graphs"""
    try:
        if 'old_file' not in request.files or 'new_file' not in request.files:
            return jsonify({'error': 'Both old and new flame graph files are required'}), 400
        
        old_file = request.files['old_file']
        new_file = request.files['new_file']
        
        if old_file.filename == '' or new_file.filename == '':
            return jsonify({'error': 'Please select both files'}), 400
        
        # Read file contents
        old_content = old_file.read().decode('utf-8')
        new_content = new_file.read().decode('utf-8')
        
        # Parse flame graphs
        old_stacks, old_total = parser.parse_file(old_content)
        new_stacks, new_total = parser.parse_file(new_content)
        
        # Calculate differences
        diff_results = differ.calculate_diff(old_stacks, new_stacks, old_total, new_total)
        
        return jsonify({
            'success': True,
            'data': diff_results
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/health')
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'})

@app.route('/api/debug-stacks', methods=['POST'])
def debug_stacks():
    """Debug endpoint to analyze similar stack traces"""
    try:
        if 'old_file' not in request.files or 'new_file' not in request.files:
            return jsonify({'error': 'Both old and new flame graph files are required'}), 400
        
        old_file = request.files['old_file']
        new_file = request.files['new_file']
        
        # Read and parse files
        old_content = old_file.read().decode('utf-8')
        new_content = new_file.read().decode('utf-8')
        old_stacks, old_total = parser.parse_file(old_content)
        new_stacks, new_total = parser.parse_file(new_content)
        
        # Calculate differences
        diff_results = differ.calculate_diff(old_stacks, new_stacks, old_total, new_total)
        
        # Find similar stack traces between added/removed
        added_methods = set()
        removed_methods = set()
        
        for item in diff_results['added']:
            # Extract last method from stack trace
            stack_parts = item['stack'].split(';')
            if stack_parts:
                last_method = stack_parts[-1].split('/')[-1] if '/' in stack_parts[-1] else stack_parts[-1]
                added_methods.add(last_method)
        
        for item in diff_results['removed']:
            stack_parts = item['stack'].split(';')
            if stack_parts:
                last_method = stack_parts[-1].split('/')[-1] if '/' in stack_parts[-1] else stack_parts[-1]
                removed_methods.add(last_method)
        
        # Find overlapping methods
        common_methods = added_methods.intersection(removed_methods)
        
        # Get examples of similar stacks
        examples = []
        for method in list(common_methods)[:5]:  # Limit to 5 examples
            added_examples = [item for item in diff_results['added'] if method in item['stack']]
            removed_examples = [item for item in diff_results['removed'] if method in item['stack']]
            
            if added_examples and removed_examples:
                examples.append({
                    'method': method,
                    'added_example': added_examples[0]['stack'],
                    'removed_example': removed_examples[0]['stack']
                })
        
        return jsonify({
            'success': True,
            'data': {
                'common_methods_count': len(common_methods),
                'common_methods': list(common_methods)[:10],  # Limit output
                'examples': examples,
                'summary': {
                    'total_added': len(diff_results['added']),
                    'total_removed': len(diff_results['removed']),
                    'total_increased': len(diff_results['increased']),
                    'total_decreased': len(diff_results['decreased'])
                }
            }
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static', exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000)
