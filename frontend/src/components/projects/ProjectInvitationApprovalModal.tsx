"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { Check, Loader2, ShieldCheck, Trash2, X } from "lucide-react";
import Modal from "@/components/ui/Modal";
import { ProjectAPI, ProjectInvitationData, ProjectData } from "@/lib/api/projectApi";

type ProjectInvitationApprovalModalProps = {
  isOpen: boolean;
  onClose: () => void;
  project: ProjectData | null;
};

const formatInviter = (invitation: ProjectInvitationData) => {
  const inviter = invitation.invited_by;
  if (!inviter) return "Unknown";
  return inviter.name || inviter.username || inviter.email || `User #${inviter.id}`;
};

export default function ProjectInvitationApprovalModal({
  isOpen,
  onClose,
  project,
}: ProjectInvitationApprovalModalProps) {
  const [invitations, setInvitations] = useState<ProjectInvitationData[]>([]);
  const [loading, setLoading] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);

  const projectId = useMemo(() => project?.id ?? null, [project]);

  const loadInvitations = useCallback(async () => {
    if (!projectId) return;
    try {
      setLoading(true);
      const data = await ProjectAPI.getPendingInvitationApprovals(projectId);
      setInvitations(data || []);
    } catch (err: any) {
      const message =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Failed to load invitations.";
      toast.error(message);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    if (!isOpen) return;
    loadInvitations();
  }, [isOpen, loadInvitations]);

  const handleApprove = async (invitationId: number) => {
    if (!projectId) return;
    try {
      setActingId(invitationId);
      await ProjectAPI.approveProjectInvitation(projectId, invitationId);
      toast.success("Invitation approved and sent.");
      setInvitations((prev) => prev.filter((item) => item.id !== invitationId));
    } catch (err: any) {
      const message =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Failed to approve invitation.";
      toast.error(message);
    } finally {
      setActingId(null);
    }
  };

  const handleReject = async (invitationId: number) => {
    if (!projectId) return;
    if (!window.confirm("Reject this invitation?")) return;
    try {
      setActingId(invitationId);
      await ProjectAPI.rejectProjectInvitation(projectId, invitationId);
      toast.success("Invitation rejected.");
      setInvitations((prev) => prev.filter((item) => item.id !== invitationId));
    } catch (err: any) {
      const message =
        err?.response?.data?.detail ||
        err?.response?.data?.message ||
        err?.message ||
        "Failed to reject invitation.";
      toast.error(message);
    } finally {
      setActingId(null);
    }
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} ariaLabel="Invitation Approvals">
      <div className="w-full max-w-3xl overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-[0_20px_60px_-40px_rgba(15,23,42,0.6)]">
        <div className="flex items-center justify-between border-b border-gray-100 bg-gradient-to-r from-white via-slate-50 to-blue-50 px-6 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-gradient-to-br from-slate-900 to-[#2AB5BD] text-white shadow-sm">
              <ShieldCheck className="h-5 w-5" />
            </div>
            <div>
              <p className="text-sm font-semibold text-gray-900">
                Invitation Approvals
              </p>
              <p className="text-xs text-gray-500">
                {project?.name || "Project"} · Owner Review Queue
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-2 text-gray-500 hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="px-6 py-5">
          <div className="rounded-2xl border border-gray-200 bg-gradient-to-br from-white via-white to-blue-50/60 p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold text-gray-900">
                Pending approvals
              </p>
              <button
                type="button"
                onClick={loadInvitations}
                className="text-xs font-medium text-[#3CCED7] hover:text-[#1a9ba3]"
              >
                Refresh
              </button>
            </div>
            <p className="mt-1 text-xs text-gray-500">
              Approving sends the invitation email. The member appears after they accept.
            </p>

            {loading ? (
              <div className="mt-6 flex items-center justify-center gap-2 text-sm text-gray-500">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading invitations...
              </div>
            ) : invitations.length === 0 ? (
              <div className="mt-6 rounded-2xl border border-dashed border-gray-200 bg-white px-4 py-6 text-center text-sm text-gray-500">
                No pending invitations to approve.
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                {invitations.map((invitation) => (
                  <div
                    key={invitation.id}
                    className="flex flex-col gap-3 rounded-2xl border border-gray-200 bg-white p-4 shadow-sm md:flex-row md:items-center md:justify-between"
                  >
                    <div className="space-y-1">
                      <p className="text-sm font-semibold text-gray-900">
                        {invitation.email}
                      </p>
                      <p className="text-xs text-gray-500">
                        Role:{" "}
                        <span className="font-medium text-gray-700">
                          {invitation.role}
                        </span>
                      </p>
                      <p className="text-xs text-gray-500">
                        Invited by {formatInviter(invitation)}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => handleApprove(invitation.id)}
                        disabled={actingId === invitation.id}
                        className="inline-flex items-center gap-2 rounded-full bg-emerald-600 px-4 py-2 text-xs font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-70"
                      >
                        {actingId === invitation.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Check className="h-4 w-4" />
                        )}
                        Approve
                      </button>
                      <button
                        type="button"
                        onClick={() => handleReject(invitation.id)}
                        disabled={actingId === invitation.id}
                        className="inline-flex items-center gap-2 rounded-full border border-gray-200 px-4 py-2 text-xs font-semibold text-gray-700 transition hover:border-red-200 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-70"
                      >
                        <Trash2 className="h-4 w-4" />
                        Reject
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </Modal>
  );
}
