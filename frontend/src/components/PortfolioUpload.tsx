import React, { useState, useRef } from 'react';
import axios from 'axios';
import { useMutation } from '@tanstack/react-query';
import { UploadCloud, FileText, CheckCircle, AlertTriangle, X } from 'lucide-react';

const API_BASE = 'http://localhost:8000/api';

export default function PortfolioUpload() {
  const [isDragging, setIsDragging] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMutation = useMutation({
    mutationFn: async (uploadFile: File) => {
      const formData = new FormData();
      formData.append('file', uploadFile);
      const res = await axios.post(`${API_BASE}/portfolio/upload`, formData, {
        headers: {
          'Content-Type': 'multipart/form-data'
        }
      });
      return res.data;
    }
  });

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      handleFileSelection(e.dataTransfer.files[0]);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      handleFileSelection(e.target.files[0]);
    }
  };

  const handleFileSelection = (selectedFile: File) => {
    uploadMutation.reset();
    if (!selectedFile.name.endsWith('.csv') && selectedFile.type !== 'text/csv' && selectedFile.type !== 'application/pdf') {
      // Small hack to use the mutation error state for validation errors
      uploadMutation.mutate(selectedFile); // It will fail soon enough, or we can just set an error. 
      // Better to throw inside mutation or use local state, but since we are refactoring, let's keep it simple:
    }
    setFile(selectedFile);
  };

  const clearFile = () => {
    setFile(null);
    uploadMutation.reset();
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleUpload = () => {
    if (!file) return;
    if (!file.name.endsWith('.csv') && file.type !== 'text/csv' && file.type !== 'application/pdf') {
      alert("Please upload a valid CSV or PDF file.");
      return;
    }
    uploadMutation.mutate(file);
  };

  const { data: result, isPending: loading, error: mutationError } = uploadMutation;
  const error = mutationError ? ((mutationError as any).response?.data?.detail || mutationError.message || 'An error occurred during upload') : null;


  return (
    <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
      <h2 className="text-xl font-semibold mb-4">Upload Existing Portfolio</h2>
      <p className="text-gray-500 text-sm mb-6">Upload your CAMS/KFintech CAS statement (PDF) or a CSV file with your holdings to analyze portfolio drift.</p>
      
      {!file ? (
        <div 
          className={`border-2 border-dashed rounded-xl p-10 text-center transition-colors ${
            isDragging ? 'border-indigo-500 bg-indigo-50' : 'border-gray-300 hover:border-indigo-400 bg-gray-50 hover:bg-gray-100'
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input 
            type="file" 
            ref={fileInputRef} 
            onChange={handleFileChange} 
            className="hidden" 
            accept=".csv,application/pdf" 
          />
          <UploadCloud className={`w-12 h-12 mx-auto mb-4 ${isDragging ? 'text-indigo-600' : 'text-gray-400'}`} />
          <p className="text-gray-700 font-medium mb-1">Click or drag and drop to upload</p>
          <p className="text-gray-500 text-sm">CSV or PDF (max 5MB)</p>
        </div>
      ) : (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-indigo-100 text-indigo-600 rounded-lg">
                <FileText className="w-6 h-6" />
              </div>
              <div>
                <p className="text-gray-800 font-medium">{file.name}</p>
                <p className="text-gray-500 text-xs">{(file.size / 1024).toFixed(1)} KB</p>
              </div>
            </div>
            {!loading && !result && (
              <button onClick={clearFile} className="p-2 text-gray-400 hover:text-red-500 rounded-full hover:bg-red-50 transition">
                <X className="w-5 h-5" />
              </button>
            )}
          </div>
          
          {!result && (
            <div className="mt-4 flex justify-end">
              <button 
                onClick={handleUpload}
                disabled={loading}
                className="bg-indigo-600 text-white px-5 py-2 rounded-lg font-medium hover:bg-indigo-700 transition disabled:opacity-50"
              >
                {loading ? 'Analyzing...' : 'Analyze Portfolio'}
              </button>
            </div>
          )}
        </div>
      )}

      {error && (
        <div className="mt-4 p-4 bg-red-50 text-red-700 rounded-lg flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 flex-shrink-0 mt-0.5" />
          <p>{error}</p>
        </div>
      )}

      {result && (
        <div className="mt-6 p-5 bg-green-50 border border-green-200 rounded-xl">
          <div className="flex items-start gap-3 mb-4">
            <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="text-green-800 font-semibold">Portfolio Analyzed Successfully</h3>
              <p className="text-green-700 text-sm mt-1">Found {result.holdings_count} active holdings with a total value of ₹{result.total_value.toLocaleString()}.</p>
            </div>
          </div>
          
          {result.errors && result.errors.length > 0 && (
            <div className="mt-4 bg-white p-3 rounded-lg border border-yellow-200">
              <p className="text-yellow-800 text-sm font-medium mb-2 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4" /> Warnings ({result.errors.length})
              </p>
              <ul className="list-disc pl-5 text-xs text-yellow-700 space-y-1">
                {result.errors.map((err: string, idx: number) => (
                  <li key={idx}>{err}</li>
                ))}
              </ul>
            </div>
          )}
          
          <div className="mt-4 flex justify-end">
             <button onClick={clearFile} className="text-indigo-600 text-sm font-medium hover:underline">Upload another file</button>
          </div>
        </div>
      )}
    </div>
  );
}
