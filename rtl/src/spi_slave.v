// SPI Slave — minimal interface for sensor data input and estimate output
//
// Protocol:
//   - Master sends 6 bytes: 3 sensor measurements × 2 bytes each
//     Each measurement: [sign(1b) + mag(7b padding)] [mag(8b)]
//     Simplified: byte 0 = {7'b0, sign}, byte 1 = mag
//   - After receiving 6 bytes, triggers a PF step
//   - Master can then clock out 2 bytes of estimate: [sign] [mag]
//
// SPI mode 0 (CPOL=0, CPHA=0): sample on rising SCLK edge

`include "lns8_pkg.v"

module spi_slave (
    input  wire        clk,       // system clock
    input  wire        rst_n,

    // SPI pins
    input  wire        spi_sclk,
    input  wire        spi_mosi,
    output reg         spi_miso,
    input  wire        spi_cs_n,  // active low

    // Sensor data output (to register file)
    output reg  [2:0]  sensor_idx,
    output reg         sensor_sign,
    output reg  [7:0]  sensor_mag,
    output reg         sensor_valid,   // pulse when new measurement ready

    // PF trigger
    output reg         pf_trigger,     // pulse to start PF step

    // Estimate input (from PF)
    input  wire        est_sign,
    input  wire [7:0]  est_mag,
    input  wire        est_valid       // PF step complete
);

    // Synchronize SPI signals to system clock
    reg [2:0] sclk_sync, mosi_sync, cs_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sclk_sync <= 3'b0;
            mosi_sync <= 3'b0;
            cs_sync   <= 3'b111;
        end else begin
            sclk_sync <= {sclk_sync[1:0], spi_sclk};
            mosi_sync <= {mosi_sync[1:0], spi_mosi};
            cs_sync   <= {cs_sync[1:0], spi_cs_n};
        end
    end

    wire sclk_rise = (sclk_sync[2:1] == 2'b01);
    wire sclk_fall = (sclk_sync[2:1] == 2'b10);
    wire cs_active = ~cs_sync[2];
    wire mosi_bit  = mosi_sync[2];

    // Shift register
    reg [7:0]  shift_in;
    reg [2:0]  bit_cnt;
    reg [3:0]  byte_cnt;     // 0..5 = sensor bytes, 6..7 = read estimate

    // Latched estimate for output
    reg        est_sign_r;
    reg [7:0]  est_mag_r;

    // TX shift register
    reg [7:0]  shift_out;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            shift_in     <= 8'h0;
            bit_cnt      <= 3'd0;
            byte_cnt     <= 4'd0;
            sensor_idx   <= 3'd0;
            sensor_sign  <= 1'b0;
            sensor_mag   <= 8'h0;
            sensor_valid <= 1'b0;
            pf_trigger   <= 1'b0;
            spi_miso     <= 1'b0;
            shift_out    <= 8'h0;
            est_sign_r   <= 1'b0;
            est_mag_r    <= 8'h0;
        end else begin
            sensor_valid <= 1'b0;
            pf_trigger   <= 1'b0;

            // Latch estimate when ready
            if (est_valid) begin
                est_sign_r <= est_sign;
                est_mag_r  <= est_mag;
            end

            if (!cs_active) begin
                // CS deasserted — reset
                bit_cnt  <= 3'd0;
                byte_cnt <= 4'd0;
            end else begin
                // Rising edge: sample MOSI
                if (sclk_rise) begin
                    shift_in <= {shift_in[6:0], mosi_bit};
                    bit_cnt  <= bit_cnt + 3'd1;

                    if (bit_cnt == 3'd7) begin
                        // Full byte received
                        case (byte_cnt)
                            // Sensor 0: bytes 0,1
                            4'd0: sensor_sign <= shift_in[0]; // sign in LSB of first byte
                            4'd1: begin
                                sensor_mag   <= {shift_in[6:0], mosi_bit};
                                sensor_idx   <= 3'd0;
                                sensor_valid <= 1'b1;
                            end
                            // Sensor 1: bytes 2,3
                            4'd2: sensor_sign <= shift_in[0];
                            4'd3: begin
                                sensor_mag   <= {shift_in[6:0], mosi_bit};
                                sensor_idx   <= 3'd1;
                                sensor_valid <= 1'b1;
                            end
                            // Sensor 2: bytes 4,5
                            4'd4: sensor_sign <= shift_in[0];
                            4'd5: begin
                                sensor_mag   <= {shift_in[6:0], mosi_bit};
                                sensor_idx   <= 3'd2;
                                sensor_valid <= 1'b1;
                                pf_trigger   <= 1'b1;  // trigger PF after last sensor
                            end
                            default: ;
                        endcase

                        byte_cnt <= byte_cnt + 4'd1;
                    end
                end

                // Falling edge: shift out MISO
                if (sclk_fall) begin
                    if (byte_cnt == 4'd6) begin
                        // Output estimate sign byte
                        if (bit_cnt == 3'd0)
                            shift_out <= {7'b0, est_sign_r};
                        spi_miso <= shift_out[7];
                        shift_out <= {shift_out[6:0], 1'b0};
                    end else if (byte_cnt == 4'd7) begin
                        // Output estimate magnitude
                        if (bit_cnt == 3'd0)
                            shift_out <= est_mag_r;
                        spi_miso <= shift_out[7];
                        shift_out <= {shift_out[6:0], 1'b0};
                    end
                end
            end
        end
    end

endmodule
