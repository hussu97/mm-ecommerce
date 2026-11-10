/**
 * Bindings for employees, their documents and the expiring-paperwork warning.
 * Super-admin only on the API; types come from the generated contract.
 */
import type { Schemas } from '@mm/types';
import { api, buildQs, request } from './api';

export type Employee = Schemas['EmployeeResponse'];
export type EmployeeListItem = Schemas['EmployeeListItem'];
export type EmployeeCreate = Schemas['EmployeeCreate'];
export type EmployeeUpdate = Schemas['EmployeeUpdate'];
export type EmployeeDocument = Schemas['EmployeeDocumentResponse'];
export type EmployeeDocumentCreate = Schemas['EmployeeDocumentCreate'];
export type EmployeeDocumentUpdate = Schemas['EmployeeDocumentUpdate'];
export type EmployeeDocumentTypeSpec = Schemas['EmployeeDocumentTypeSpec'];
export type ExpiringDocument = Schemas['ExpiringDocument'];
export type ExpiringDocuments = Schemas['ExpiringDocumentsResponse'];
export type SignedDocumentUrl = Schemas['SignedDocumentUrl'];
export type EmployeeDocType = EmployeeDocumentCreate['doc_type'];

/** Raw bytes to the private bucket; signed on view, never public. */
function uploadRaw<T>(path: string, file: File) {
  return request<T>(path, { method: 'POST', body: file, headers: { 'Content-Type': file.type } });
}

export const employeesApi = {
  documentTypes: () => api.get<EmployeeDocumentTypeSpec[]>('/employees/document-types'),
  list: (status: 'active' | 'left' | 'all' = 'active', search?: string) =>
    api.get<EmployeeListItem[]>(`/employees${buildQs({ status, search: search || undefined })}`),
  get: (id: string) => api.get<Employee>(`/employees/${id}`),
  create: (d: EmployeeCreate) => api.post<Employee>('/employees', d),
  update: (id: string, d: EmployeeUpdate) => api.patch<Employee>(`/employees/${id}`, d),
  remove: (id: string) => api.delete<void>(`/employees/${id}`),

  uploadPhoto: (id: string, file: File) => uploadRaw<Employee>(`/employees/${id}/photo`, file),
  photoUrl: (id: string) => api.get<SignedDocumentUrl>(`/employees/${id}/photo`),
  removePhoto: (id: string) => api.delete<Employee>(`/employees/${id}/photo`),

  createDocument: (id: string, d: EmployeeDocumentCreate) =>
    api.post<EmployeeDocument>(`/employees/${id}/documents`, d),
  updateDocument: (id: string, docId: string, d: EmployeeDocumentUpdate) =>
    api.patch<EmployeeDocument>(`/employees/${id}/documents/${docId}`, d),
  removeDocument: (id: string, docId: string) =>
    api.delete<void>(`/employees/${id}/documents/${docId}`),
  uploadDocumentFile: (id: string, docId: string, file: File) =>
    uploadRaw<EmployeeDocument>(`/employees/${id}/documents/${docId}/file`, file),
  documentFileUrl: (id: string, docId: string) =>
    api.get<SignedDocumentUrl>(`/employees/${id}/documents/${docId}/file`),
  removeDocumentFile: (id: string, docId: string) =>
    api.delete<EmployeeDocument>(`/employees/${id}/documents/${docId}/file`),
};

export const complianceApi = {
  expiringDocuments: (withinDays = 15) =>
    api.get<ExpiringDocuments>(`/compliance/expiring-documents${buildQs({ within_days: withinDays })}`),
};
