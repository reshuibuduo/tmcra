import { z } from "zod";

const LOOPBACK_ADDRESS_RE = /^127\.0\.0\.1:(\d{1,5})$/;

const bindingsSchema = z.array(
  z.object({
    name: z.string().min(1),
    type: z.literal("tmcra_loopback_api"),
    options: z
      .object({
        address: z.string(),
      })
      .passthrough(),
  }),
);

function externalServiceName(workerIndex, bindingName) {
  return `tmcra:loopback-api:${workerIndex}:${bindingName}`;
}

function validatedAddress(rawAddress) {
  const address = String(rawAddress ?? "").trim();
  const match = LOOPBACK_ADDRESS_RE.exec(address);
  const port = Number(match?.[1] ?? 0);
  if (!match || !Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error("TMCRA loopback API binding must target a literal 127.0.0.1 TCP port.");
  }
  return address;
}

const TMCRA_LOOPBACK_API = {
  options: bindingsSchema,
  bindingTypeDescription: "TMCRA loopback API fetcher",
  getBindings(options, workerIndex) {
    return options.map((binding) => ({
      name: binding.name,
      service: {
        name: externalServiceName(workerIndex, binding.name),
      },
    }));
  },
  getNodeBindings() {
    return {};
  },
  getServices({ options, workerIndex }) {
    return options.map((binding) => ({
      name: externalServiceName(workerIndex, binding.name),
      external: {
        address: validatedAddress(binding.options.address),
        http: {},
      },
    }));
  },
};

export const plugins = { TMCRA_LOOPBACK_API };
