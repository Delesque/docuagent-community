import type { ArchitectureEdgeOperation, ArchitectureEditResult, ArchitectureEditStreamEvent, ArchitectureNodeOperation, ArchitectureNodeResult, ArchitectureRequirementResult, ArchitectureUndoResult, BootstrapState, BootstrapStreamEvent, InterviewMode, ModelCallOptions, NodeAttachments, ProvenanceAction, ProvenanceUpdateResult, ProviderConfig, TaskItem } from "./types";
import { post, postWithRetry, readNdjsonEvents } from "./shared";

export async function bootstrapStart(request: {
  path: string;
  name: string;
  description: string;
  interview_mode?: InterviewMode;
  provider: ProviderConfig;
}, options: ModelCallOptions = {}): Promise<BootstrapState> {
  return postWithRetry("/api/bootstrap/start", request, options);
}

export async function bootstrapAnswer(request: {
  path: string;
  answer: string;
  provider: ProviderConfig;
}, options: ModelCallOptions = {}): Promise<BootstrapState> {
  return postWithRetry("/api/bootstrap/answer", request, options);
}

export async function bootstrapRevise(request: {
  path: string;
  feedback: string;
  provider: ProviderConfig;
}, options: ModelCallOptions = {}): Promise<BootstrapState> {
  return postWithRetry("/api/bootstrap/revise", request, options);
}

export async function bootstrapConfirm(request: {
  path: string;
  provider: ProviderConfig;
}, options: ModelCallOptions = {}): Promise<BootstrapState> {
  return postWithRetry("/api/bootstrap/confirm", request, options);
}

export async function bootstrapFinalize(request: {
  path: string;
  provider: ProviderConfig;
}, options: ModelCallOptions = {}): Promise<BootstrapState> {
  return postWithRetry("/api/bootstrap/finalize", request, options);
}

export async function editArchitecture(request: {
  path: string;
  request: string;
  provider: ProviderConfig;
}, options: ModelCallOptions = {}): Promise<ArchitectureEditResult> {
  return postWithRetry("/api/architecture/edit", request, options);
}

export async function undoArchitecture(path: string): Promise<ArchitectureUndoResult> {
  return post("/api/architecture/undo", { path });
}

export async function updateModuleRequirement(
  path: string,
  moduleId: string,
  requirement: string,
): Promise<ArchitectureRequirementResult> {
  return post("/api/architecture/update-module-requirement", {
    path,
    module_id: moduleId,
    requirement,
  });
}
export async function updateProvenance(
  path: string,
  claimId: string,
  action: ProvenanceAction,
  text?: string,
): Promise<ProvenanceUpdateResult> {
  return post("/api/provenance/update", { path, claim_id: claimId, action, text });
}
export async function updateArchitectureNodes(
  path: string,
  operation: ArchitectureNodeOperation,
): Promise<ArchitectureNodeResult> {
  return post("/api/architecture/nodes", { path, ...operation });
}
export async function updateArchitectureEdges(
  path: string,
  operation: ArchitectureEdgeOperation,
): Promise<ArchitectureNodeResult> {
  return post("/api/architecture/edges", { path, ...operation });
}
export async function reopenArchitectureReview(path: string): Promise<BootstrapState> {
  return post("/api/architecture/reopen-review", { path });
}
export async function setInterviewMode(path:string,interview_mode:InterviewMode):Promise<BootstrapState>{return post("/api/bootstrap/mode",{path,interview_mode});}
export async function streamBootstrapStart(request:{path:string;name:string;description:string;interview_mode?:InterviewMode;provider:ProviderConfig},onEvent:(event:BootstrapStreamEvent)=>void,signal?:AbortSignal):Promise<BootstrapState>{let result:BootstrapState|null=null;await readNdjsonEvents("/api/bootstrap/stream-start",request,(event:BootstrapStreamEvent)=>{onEvent(event);if(event.type==="done"&&event.state)result=event.state;},signal);if(!result)throw new Error("流式初始化未返回完成事件。");return result;}
export async function streamBootstrapAnswer(request:{path:string;answer:string;provider:ProviderConfig},onEvent:(event:BootstrapStreamEvent)=>void,signal?:AbortSignal):Promise<BootstrapState>{let result:BootstrapState|null=null;await readNdjsonEvents("/api/bootstrap/stream-answer",request,(event:BootstrapStreamEvent)=>{onEvent(event);if(event.type==="done"&&event.state)result=event.state;},signal);if(!result)throw new Error("流式回答未返回完成事件。");return result;}
export async function streamEditArchitecture(request:{path:string;request:string;provider:ProviderConfig},onEvent:(event:ArchitectureEditStreamEvent)=>void,signal?:AbortSignal):Promise<ArchitectureEditResult>{let result:ArchitectureEditResult|null=null;await readNdjsonEvents("/api/architecture/stream-edit",request,(event:ArchitectureEditStreamEvent)=>{onEvent(event);if(event.type==="done"&&event.result)result=event.result;},signal);if(!result)throw new Error("流式修改未返回完成事件。");return result;}
export async function acceptSuggestion(path:string,moduleId:string,attachmentId:string):Promise<{attachments:NodeAttachments;task:TaskItem}>{return post("/api/suggestions/accept",{path,module_id:moduleId,attachment_id:attachmentId});}
export async function rejectSuggestion(path:string,moduleId:string,attachmentId:string):Promise<{attachments:NodeAttachments}>{return post("/api/suggestions/reject",{path,module_id:moduleId,attachment_id:attachmentId});}
