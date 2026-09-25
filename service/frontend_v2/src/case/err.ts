/** API error (contract: {error:{code,message,details}}); UNAVAILABLE = the service does not answer. */
export class ApiErr extends Error {
  code: string;
  status: number;
  details: any;
  constructor(code: string, message: string, status = 0, details: any = null) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}
