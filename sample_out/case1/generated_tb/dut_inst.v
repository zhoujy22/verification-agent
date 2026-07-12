// Auto-generated wrapper for stream_dut
`timescale 1ns/1ps
module tb_top(clk, rst_n, in_valid, in_ready, in_data, out_valid, out_ready, out_data);
  input  wire clk
  input  wire rst_n
  input  wire in_valid
  output wire in_ready
  input  wire [7:0] in_data
  output wire out_valid
  input  wire out_ready
  output wire [7:0] out_data
  stream_dut dut_inst (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_ready(in_ready), .in_data(in_data), .out_valid(out_valid), .out_ready(out_ready), .out_data(out_data));
endmodule
