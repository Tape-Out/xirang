/* 由 regmap.yaml 生成，勿手改。 */
#ifndef SPI_H
#define SPI_H

#define SPI_BASE 268455936
#define SPI_SCKDIV 0x0000  /* clock divisor */
#define SPI_SCKMODE 0x0004  /* clock polarity and phase */
#define SPI_CSID 0x0010  /* chip select index */
#define SPI_CSDEF 0x0014  /* chip select inactive levels */
#define SPI_CSMODE 0x0018  /* chip select behaviour */
#define SPI_DELAY0 0x0028  /* chip select to clock, and clock to chip select release */
#define SPI_DELAY1 0x002c  /* chip select inactive time, and gap between held frames */
#define SPI_FMT 0x0040  /* frame format */
#define SPI_TXDATA 0x0048  /* transmit, write pushes */
#define SPI_RXDATA 0x004c  /* receive, read pops */
#define SPI_TXMARK 0x0050  /* transmit watermark threshold */
#define SPI_RXMARK 0x0054  /* receive watermark threshold */
#define SPI_IE 0x0070  /* interrupt enable */
#define SPI_IP 0x0074  /* interrupt pending */

#endif
