// Minimal valid/ready stream DUT for end-to-end smoke testing.
// 2-deep skid buffer, parameterizable width.

`timescale 1ns/1ps

module stream_dut
  #(parameter WIDTH = 8, parameter DEPTH = 2)
   (
    input              clk,
    input              rst_n,
    input              in_valid,
    output             in_ready,
    input  [WIDTH-1:0] in_data,
    output             out_valid,
    input              out_ready,
    output [WIDTH-1:0] out_data
   );

    reg [WIDTH-1:0] fifo [0:DEPTH-1];
    reg [$clog2(DEPTH+1)-1:0] count;
    reg [WIDTH-1:0] head_data;
    reg head_valid;

    assign in_ready  = (count < DEPTH);
    assign out_valid = head_valid;
    assign out_data  = head_data;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count      <= 0;
            head_valid <= 0;
        end else begin
            case ({in_valid && in_ready, out_valid && out_ready})
                2'b10: begin                 // push only
                    fifo[count] <= in_data;
                    count <= count + 1;
                end
                2'b01: begin                 // pop only
                    count      <= count - 1;
                    head_data  <= fifo[1];
                    head_valid <= (count > 1);
                end
                2'b11: begin                 // push+pop (same cycle)
                    fifo[0] <= in_data;
                    if (count > 1) begin
                        head_data  <= fifo[1];
                        head_valid <= 1'b1;
                    end else begin
                        head_data  <= in_data;
                        head_valid <= 1'b1;
                    end
                end
                default: ;                   // idle
            endcase
        end
    end
endmodule
